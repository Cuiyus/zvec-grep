"""No network, Docker or inference: remote phase gates and credential boundaries."""
import contextlib
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import ci_run as ci
import probe_mcp
import remote_embedding as remote
import run as pilot
from test_run import assistant, event_stream, init_event, result

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIXTURE_KEY = 'fixture-embedding-key-not-real'
ENV = {'ZVEC_GREP_API_KEY': FIXTURE_KEY, 'ZVEC_GREP_ENDPOINT': remote.ENDPOINT}


def plan_fixture(root, frozen=True):
    here = root / 'benchmarks/claude-qa-pilot'
    here.mkdir(parents=True)
    plan = json.loads((HERE / remote.PLAN_NAME).read_text())
    for item in [plan['base_experiment'], plan['task']['question'], plan['task']['rubric'], plan['completion_hook'], plan['dataset'], plan['configuration_research']]:
        target = root / item['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / item['path'], target)
    plan['frozen'] = frozen
    path = here / remote.PLAN_NAME
    path.write_text(json.dumps(plan))
    return here, path, plan


def argv(root, task='zvec-q001', *, root_guidance=False, budget=True,
         confirmation=False, expansion=False, repeats=1):
    values = ['ci_run.py', '--task', task, '--remote-embedding-pilot',
              '--work', str(root / 'work'), '--reports', str(root / 'reports'),
              '--repeats', str(repeats)]
    for flag, enabled in [('--workspace-root-guidance', root_guidance),
                          ('--completion-budget', budget),
                          ('--completion-confirmation', confirmation),
                          ('--closed-issue-expansion', expansion)]:
        if enabled:
            values.append(flag)
    return values


def index_fixture(index):
    manifest = {'root': '/workspace', 'indexPolicy': 'enabled', 'embeddings': [{
        'model': {'provider': 'qwen', 'name': 'qwen3.7-text-embedding', 'endpoint': remote.ENDPOINT},
        'dimension': 1024, 'metric': 'cosine', 'maxBatchSize': 20, 'maxInputTokens': 128000,
        'maxImageBytes': None}], 'embeddingRuntimes': {remote.MODEL: {'endpoint': remote.ENDPOINT}}}
    (index / 'manifest.json').write_text(json.dumps(manifest))
    return manifest


class RemoteGuardTests(unittest.TestCase):
    def test_unfrozen_plan_cannot_start_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            here, _, _ = plan_fixture(root, frozen=False)
            with patch.object(sys, 'argv', argv(root)), patch.object(ci, 'HERE', here), patch.object(ci, 'ROOT', root), \
                    patch.object(ci, 'upstream_protocol') as setup, patch.object(ci, 'run') as command, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    ci.main()
                setup.assert_not_called()
                command.assert_not_called()
                self.assertFalse((root / 'work').exists())

    def test_frozen_plan_accepts_exact_contract_and_rejects_hash_or_factor_drift(self):
        changes = [None, 'prompt', 'model', 'endpoint', 'pair-count', 'instructions', 'budget', 'source']
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                here, path, plan = plan_fixture(root)
                if change == 'prompt':
                    (root / plan['task']['question']['path']).write_text('drift')
                elif change == 'model':
                    plan['retrieval']['embedding_model'] = 'local/potion-code-16m-v2'
                elif change == 'endpoint':
                    plan['retrieval']['embedding_endpoint'] = 'https://fixture.invalid'
                elif change == 'pair-count':
                    plan['run_plan']['repeats'] = 2
                elif change == 'instructions':
                    plan['common_instructions_sha256'] = 'drift'
                elif change == 'budget':
                    plan['completion_control']['tool_budget'] = 25
                elif change == 'source':
                    plan['task']['revision'] = '0' * 40
                path.write_text(json.dumps(plan))
                if change is None:
                    self.assertEqual(remote.validate_plan(root, here)['sha256'], remote.digest(path))
                else:
                    with self.assertRaises(ValueError):
                        remote.validate_plan(root, here)

    def test_exact_endpoint_key_presence_and_no_alternate_fallback(self):
        environments = [ENV, {}, {'ZVEC_GREP_ENDPOINT': remote.ENDPOINT},
                        dict(ENV, ZVEC_GREP_ENDPOINT='https://fixture.invalid'),
                        dict(ENV, QWEN_API_KEY='alternate-fixture'),
                        dict(ENV, ZVEC_GREP_EMBEDDING_ENDPOINT='https://fixture.invalid')]
        for index, environment in enumerate(environments):
            with self.subTest(index=index), patch.dict(os.environ, environment, clear=True):
                if index == 0:
                    remote.validate_environment()
                else:
                    with self.assertRaises(ValueError):
                        remote.validate_environment()

    def test_wrapper_rejects_every_nonexclusive_phase_before_setup(self):
        for task, root_flag, budget, confirmation, expansion, repeats in itertools.product(
                ('reflex-6', 'zvec-q001', 'zvec-q002', 'zvec-q003'), *[(False, True)] * 4, (1, 2)):
            allowed = task == 'zvec-q001' and not root_flag and budget and not confirmation and not expansion and repeats == 1
            with self.subTest(task=task, flags=(root_flag,budget,confirmation,expansion,repeats)), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                args = argv(root, task, root_guidance=root_flag, budget=budget,
                            confirmation=confirmation, expansion=expansion, repeats=repeats)
                with patch.object(sys, 'argv', args), patch.object(ci, 'validate_remote_embedding_plan', side_effect=RuntimeError('gate reached')) as gate, \
                        patch.object(ci, 'upstream_protocol') as setup, contextlib.redirect_stderr(io.StringIO()):
                    if allowed:
                        with self.assertRaisesRegex(RuntimeError, 'gate reached'):
                            ci.main()
                        gate.assert_called_once()
                    else:
                        with self.assertRaises(SystemExit):
                            ci.main()
                        gate.assert_not_called()
                    setup.assert_not_called()
                    self.assertFalse((root / 'work').exists())

    def test_workflow_gate_executes_before_provider_and_rejects_unfrozen_or_missing_env(self):
        workflow = (ROOT / '.github/workflows/claude-qa-pilot.yml').read_text()
        label = '      - name: Verify remote embedding plan and environment before provider calls\n'
        self.assertLess(workflow.index(label), workflow.index('      - name: Verify Qwen Anthropic'))
        step = workflow.split(label, 1)[1].split('      - name:', 1)[0]
        self.assertIn('if: inputs.remote_embedding_pilot', step)
        self.assertIn('secrets.QWEN_EMBEDDING_API_KEY', step)
        self.assertIn('vars.QWEN_EMBEDDING_ENDPOINT', step)
        script = step.split("          python3 - <<'PY'\n", 1)[1].split('          PY\n', 1)[0]
        script = '\n'.join(line[10:] for line in script.splitlines())
        for frozen, environment in ((False, ENV), (True, ENV), (True, {})):
            with self.subTest(frozen=frozen, environment=bool(environment)), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                here, _, _ = plan_fixture(root, frozen=frozen)
                with patch.object(ci, 'ROOT', root), patch.object(ci, 'HERE', here), \
                        patch.dict(os.environ, environment, clear=True), patch.object(sys, 'path', list(sys.path)):
                    if frozen and environment:
                        exec(script, {})
                    else:
                        with self.assertRaises(ValueError):
                            exec(script, {})

    def test_workflow_selector_accepts_only_remote_single_case_flags(self):
        workflow = (ROOT / '.github/workflows/claude-qa-pilot.yml').read_text()
        script = workflow.split("          python3 - <<'PY'\n", 1)[1].split('          PY\n', 1)[0]
        script = '\n'.join(line[10:] for line in script.splitlines())
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'output'
            for task, root, budget, confirmation, expansion in itertools.product(
                    ('both', 'reflex-6', 'zvec-q001', 'zvec-expansion'), *[(False, True)] * 4):
                output.write_text('')
                env = dict(PILOT_TASK=task, PILOT_ROOT_GUIDANCE=str(root).lower(),
                           PILOT_COMPLETION_BUDGET=str(budget).lower(), PILOT_COMPLETION_CONFIRMATION=str(confirmation).lower(),
                           PILOT_ISSUE_EXPANSION=str(expansion).lower(), PILOT_REMOTE_EMBEDDING='true', GITHUB_OUTPUT=str(output))
                with self.subTest(task=task, flags=(root,budget,confirmation,expansion)), patch.dict(os.environ, env, clear=True):
                    if (task, root, budget, confirmation, expansion) == ('zvec-q001', False, True, False, False):
                        exec(script, {})
                        self.assertEqual(json.loads(output.read_text().split('=',1)[1]), ['zvec-q001'])
                    else:
                        with self.assertRaises(SystemExit):
                            exec(script, {})
                        self.assertEqual(output.read_text(), '')


class RemoteRuntimeTests(unittest.TestCase):
    def test_upstream_remote_profile_and_source_hashes_are_verified_before_indexing(self):
        for change in (None, 'remote-model', 'source-hash'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary); reports=root/'reports'; reports.mkdir()
                config=json.dumps({'embedding_models':{'local':'local/potion-code-16m-v2',
                    'remote':'fallback' if change=='remote-model' else remote.MODEL}})
                source={'path':ci.UPSTREAM_CONFIG_PATH,'commit':ci.UPSTREAM_COMMIT,
                        'sha256':'drift' if change=='source-hash' else hashlib.sha256(config.encode()).hexdigest()}
                (root/remote.PLAN_NAME).write_text(json.dumps({'retrieval':{'configuration_sources':[source]}}))
                def fake_run(command,**kwargs):
                    if 'rev-parse' in command:
                        return ci.UPSTREAM_COMMIT
                    if 'show' in command:
                        return config if command[-1].endswith(ci.UPSTREAM_CONFIG_PATH) else '*.ignore\n'
                    return ''
                with patch.object(ci,'HERE',root),patch.object(ci,'run',side_effect=fake_run):
                    if change is None:
                        model,ignore,provenance=ci.upstream_protocol(root,reports,remote=True)
                        self.assertEqual(model,remote.MODEL)
                        self.assertEqual(provenance['remote_embedding_configuration_sources'],[source])
                    else:
                        with self.assertRaises((ValueError,RuntimeError)):
                            ci.upstream_protocol(root,reports,remote=True)

    def test_stored_identity_and_private_seed_scan_fail_closed(self):
        for change in (None, 'model', 'endpoint', 'dimensions', 'credential', 'seed-credential'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                index = Path(temporary)
                manifest = index_fixture(index)
                if change == 'model':
                    manifest['embeddings'][0]['model']['name'] = 'fallback'
                elif change == 'endpoint':
                    manifest['embeddingRuntimes'][remote.MODEL]['endpoint'] = 'https://fixture.invalid'
                elif change == 'dimensions':
                    manifest['embeddings'][0]['dimension'] = 256
                elif change == 'credential':
                    manifest['embeddingRuntimes'][remote.MODEL]['apiKey'] = FIXTURE_KEY
                elif change == 'seed-credential':
                    (index / 'private-file').write_text(FIXTURE_KEY)
                (index / 'manifest.json').write_text(json.dumps(manifest))
                with patch.dict(os.environ, ENV, clear=True):
                    if change is None:
                        identity = remote.verify_index_identity(index)
                        self.assertEqual(identity['embedding']['model']['endpoint'], remote.ENDPOINT)
                        self.assertNotIn('embeddingRuntimes', identity)
                        self.assertTrue(remote.scan_seed_credentials([index], ci.SECRETS)['passed'])
                    else:
                        with self.assertRaises(ValueError):
                            remote.verify_index_identity(index)
                            remote.scan_seed_credentials([index], ci.SECRETS)

    def test_remote_wrapper_grants_then_indexes_then_probes_before_unchanged_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            here, _, plan = plan_fixture(root)
            commands = []
            def fake_run(command, **kwargs):
                commands.append((command, kwargs))
                if 'rev-parse' in command:
                    return plan['task']['revision']
                if any(str(part).endswith('prepare_corpus.py') for part in command):
                    source = Path(command[command.index('--output') + 1]); source.mkdir()
                    (source / 'fixture.cpp').write_text('int x;\n')
                if '/opt/qa/zg-provenance.json' in command:
                    return json.dumps({'commit': ci.UPSTREAM_COMMIT})
                if '--index' in command:
                    index_fixture(root / 'work/prepared-index')
                return ''
            with patch.object(sys, 'argv', argv(root)), patch.object(ci, 'ROOT', root), patch.object(ci, 'HERE', here), \
                    patch.object(ci, 'run', side_effect=fake_run), patch.dict(os.environ, ENV, clear=True), \
                    patch.object(ci, 'upstream_protocol', return_value=(remote.MODEL, '', {'index_ignore_sha256': 'fixture'})) as upstream:
                ci.main()
            upstream.assert_called_once_with((root / 'work').resolve(), (root / 'reports').resolve(), remote=True)
            grant = next(i for i,(cmd,_) in enumerate(commands) if '--auth' in cmd and 'grant' in cmd)
            index = next(i for i,(cmd,_) in enumerate(commands) if '--index' in cmd)
            probe = next(i for i,(cmd,_) in enumerate(commands) if len(cmd)>1 and cmd[1].endswith('/probe_mcp.py'))
            runner = next(i for i,(cmd,_) in enumerate(commands) if len(cmd)>1 and cmd[1].endswith('/run.py'))
            self.assertLess(grant, index); self.assertLess(index, probe); self.assertLess(probe, runner)
            for i in (grant,index):
                command = commands[i][0]
                self.assertIn('ZVEC_GREP_API_KEY', command)
                self.assertNotIn(FIXTURE_KEY, json.dumps(command))
                self.assertNotIn('--allow-remote', command)
                self.assertEqual(command[command.index('--endpoint')+1], remote.ENDPOINT)
            probe_command = commands[probe][0]
            self.assertEqual(probe_command[probe_command.index('--embedding-model')+1], remote.MODEL)
            command, options = commands[runner]
            for flag, value in {'--embedding-model':remote.MODEL,'--repeats':'1','--model':'qwen3.8-max',
                                '--max-turns':'30','--timeout':'900','--max-output-tokens':'8192',
                                '--effort':'high','--tool-budget':'24','--research-seconds':'600'}.items():
                self.assertEqual(command[command.index(flag)+1], value)
            self.assertNotIn('--workspace-root-guidance', command)
            self.assertNotIn('--start-repeat', command)
            self.assertEqual(options['timeout'], 2000)
            setup=json.loads((root/'reports/setup.json').read_text())
            self.assertEqual(setup['phase'],'remote-embedding-development')
            self.assertEqual(setup['embedding_runtime'],'remote')
            self.assertIn('network', setup['embedding_accounting']['agent'])
            self.assertEqual(setup['planned_trial_count'],2)
            self.assertTrue((root/'reports/private-seed-secret-scan.json').exists())

    def test_baseline_has_no_embedding_environment_or_seeds_but_zg_inherits_names_only(self):
        for profile, embedding in (('baseline', remote.MODEL), ('zg', remote.MODEL), ('zg','local/potion-code-16m-v2')):
            with self.subTest(profile=profile,embedding=embedding), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                args=pilot.parser().parse_args(['--source',str(root/'source'),'--prompt',str(root/'prompt'),
                    '--output',str(root/'output'),'--index-seed',str(root/'index'),'--home-seed',str(root/'seed'),
                    '--model','qwen3.8-max','--embedding-model',embedding])
                args.output.mkdir(); args.index_seed.mkdir(); args.home_seed.mkdir()
                (args.home_seed/'authorization-key').write_text('private-signing-fixture')
                args.mcp_server={'type':'stdio','command':'zg','args':['--server','--stdio','--mcp-toolset','agent']}
                captured=[]
                def popen(command, **kwargs):
                    captured.append(command)
                    home_mount=next(x for x in command if x.endswith(',dst=/root'))
                    home=Path(home_mount.split('src=',1)[1].split(',dst=',1)[0])
                    self.assertEqual((home/'.zvec-grep/authorization-key').exists(), profile=='zg')
                    kwargs['stdout'].write(event_stream(init_event(profile),assistant(),result()))
                    return Mock(returncode=0)
                with patch.dict(os.environ,dict(ENV, ANTHROPIC_API_KEY='fixture-agent-key'),clear=True), \
                        patch.object(pilot.subprocess,'Popen',side_effect=popen):
                    pilot.run_trial(args,profile,1,'Question.')
                command=captured[0]
                self.assertIn('ANTHROPIC_API_KEY',command)
                for name in remote.ENV_NAMES:
                    self.assertEqual(name in command, profile=='zg' and embedding==remote.MODEL)
                self.assertNotIn(FIXTURE_KEY,json.dumps(command))
                self.assertNotIn('fixture-agent-key',json.dumps(command))
                self.assertEqual(json.loads((args.output/f'r01-{profile}/config/mcp.json').read_text())['mcpServers'] != {}, profile=='zg')

    def test_probe_passes_embedding_env_only_for_remote_and_preserves_native_search(self):
        for embedding in ('local/potion-code-16m-v2', remote.MODEL):
            with self.subTest(embedding=embedding), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                for name in ('source','index','home'):
                    (root/name).mkdir()
                args=Mock(source=root/'source',output=root/'probe.json',index_seed=root/'index',home_seed=root/'home',
                          image=ci.IMAGE,zg='zg',timeout=120,embedding_model=embedding)
                captured=[]; sent=[]
                process=Mock(returncode=0); process.communicate.return_value=(b'',None)
                responses=[{}, {'tools':[{'name':probe_mcp.SEARCH_TOOL}]}, {'content':[{'type':'text','text':'source'}]}]
                if embedding==remote.MODEL:
                    responses.append({'content':[{'type':'text','text':'#1 matchedBy=vector source.cpp:1'}]})
                session=Mock(); session.receive.side_effect=responses; session.send.side_effect=sent.append
                with patch.dict(os.environ,ENV,clear=True),patch.object(probe_mcp.subprocess,'Popen',side_effect=lambda cmd,**kwargs: captured.append(cmd) or process), \
                        patch.object(probe_mcp,'StdioSession',return_value=session):
                    report=probe_mcp.probe(args)
                self.assertTrue(report['valid'],report)
                self.assertEqual('ZVEC_GREP_API_KEY' in captured[0],embedding==remote.MODEL)
                self.assertNotIn('ANTHROPIC_API_KEY',captured[0])
                self.assertNotIn(FIXTURE_KEY,json.dumps(report))
                search=next(item for item in sent if item.get('id')==3)
                self.assertEqual(search['params']['arguments'],{'root':'/workspace','query':probe_mcp.PROBE_QUERY,'limit':1})
                self.assertEqual('search_wall_seconds' in report,embedding==remote.MODEL)
                if embedding==remote.MODEL:
                    vector=next(item for item in sent if item.get('id')==4)
                    self.assertEqual(vector['params']['arguments'],{'root':'/workspace','vector':[probe_mcp.PROBE_QUERY],'limit':1})
                    self.assertEqual(report['native_search_calls'],2)
                    self.assertIn('vector_search_wall_seconds',report)

    def test_vector_preflight_rejects_fts_only_empty_and_error_responses(self):
        for response in ({'isError':True,'content':[{'type':'text','text':'matchedBy=vector'}]},
                         {'content':[]}, {'content':[{'type':'text','text':'#1 matchedBy=fts source.cpp:1'}]},
                         {'content':[{'type':'text','text':'source:\n9\tmatchedBy=vector'}]},
                         {'content':[{'type':'text','text':'#1 matchedBy=fts+vector source.cpp:1'}]}):
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                probe_mcp.require_vector_result(response)
        probe_mcp.require_vector_result({'content':[{'type':'text','text':'#1 matchedBy=vector source.cpp:1'}]})

    def test_new_secret_name_redacted_and_collector_never_uploads_seeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); output=root/'output'; output.mkdir()
            (output/'fixture.log').write_text(FIXTURE_KEY)
            with patch.dict(os.environ,{'QWEN_EMBEDDING_API_KEY':FIXTURE_KEY},clear=True):
                self.assertFalse(pilot.scan_output_for_secrets(output)['passed'])
                self.assertEqual(pilot.redact(FIXTURE_KEY),'[REDACTED]')
                scan=ci.collect_evidence(root/'work',output)
                self.assertTrue(scan['passed'])
                self.assertEqual((output/'fixture.log').read_text(),'[REDACTED]')


if __name__=='__main__':
    unittest.main()
