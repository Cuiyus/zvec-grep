from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pilot
from repair_v2.fake_services import FINAL_SENTINEL, CHILD_SENTINEL, QUERY
from repair_v2.integration import (OPENCODE_LINUX_SHA256, assert_can_start_next,
    fake_environment, validate_native_proof, validate_provider_command, verify_mcp_source)


class Services:
    def validate(self):
        return {'valid': True}


class IntegrationTests(unittest.TestCase):
    def test_fake_env_restricts_keys_and_dynamic_native_resolver(self):
        main = Path(os.environ['QA_MAIN_ROOT'])
        native = pilot.native(main)
        suite = native.load_suite('swe-qa-bench', tier='smoke')
        with patch.dict(os.environ, {'FORBIDDEN_TEST_KEY': 'must-not-forward'}):
            with fake_environment('http://172.17.0.1:12345/v1', 'http://172.17.0.1:12345/v1/embeddings'):
                self.assertNotIn('FORBIDDEN_TEST_KEY', os.environ)
                for profile in ('baseline', 'zvec-grep'):
                    command = native.build_harbor_command(suite, profile=profile, agent='opencode',
                        model='custom-openai/qwen3.8-max', job_name='offline-config-test')
                    config = validate_provider_command(command, 'http://172.17.0.1:12345/v1', profile)
                    config['provider']['custom-openai']['options']['baseURL'] = 'https://unexpected.invalid/v1'
                    changed = ['opencode_config=' + json.dumps(config)]
                    with self.assertRaisesRegex(ValueError, 'destination'):
                        validate_provider_command(changed, 'http://172.17.0.1:12345/v1', profile)
            self.assertEqual(os.environ['FORBIDDEN_TEST_KEY'], 'must-not-forward')

    def test_uncertain_cleanup_blocks_the_real_controller_gate(self):
        assert_can_start_next({'complete': True})
        for value in ({}, {'complete': False}, {'complete': 1}, {'complete': None}):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'cleanup_unconfirmed'):
                assert_can_start_next(value)

    def make_proof(self, root):
        collection = {'structural_valid': True, 'profiles': []}
        prepared = root / 'prepared'
        source = prepared / 'tasks/zvec-q001/environment/source'
        source.mkdir(parents=True)
        (source / 'README.md').write_text('first source line\nsecond source line\n')
        manifest = {'runtime_payloads': [{'path': 'package/bin/zg', 'derived_sha256': 'a' * 64,
                                       'derived_bytes': 10, 'mode': '0755'}]}
        for profile in ('baseline', 'zvec-grep'):
            run = root / 'run/runs' / ('q001-' + profile) / 'fixture'
            (run / 'agent').mkdir(parents=True)
            exports = run / 'qa-session-exports'
            exports.mkdir()
            root_id, child_id = profile + '_root', profile + '_child'
            calls = 5 if profile == 'baseline' else 6
            usage = {'root_session_id': root_id, 'sessions': [
                {'session_id': root_id, 'parent_session_id': None},
                {'session_id': child_id, 'parent_session_id': root_id}],
                'total': {'llm_calls': calls, 'input_tokens': 100 * calls, 'output_tokens': 50 * calls,
                          'cache_read_tokens': 60 * calls, 'reasoning_tokens': 20 * calls}}
            pilot.write(run / 'agent/session-usage.json', usage)
            parts = [{'type': 'tool', 'tool': name, 'state': {'status': 'completed'}} for name in ('read', 'task')]
            if profile == 'zvec-grep':
                parts.append({'type': 'tool', 'tool': 'zvec_grep_zvec_grep_search', 'state': {
                    'status': 'completed', 'input': {'root': '/workspace', 'query': QUERY},
                    'output': 'freshness: fresh\n#1 matchedBy=fts+vector README.md:1-2\nsource:\n1\tfirst source line\n2\tsecond source line'}})
            pilot.write(exports / (root_id + '.json'), {'messages': [{'info': {'role': 'assistant'}, 'parts': parts}]})
            pilot.write(exports / (child_id + '.json'), {'messages': [{'info': {'role': 'assistant'},
                'parts': [{'type': 'text', 'text': CHILD_SENTINEL}, {'type': 'tool', 'tool': 'read', 'state': {'status': 'completed'}}]}]})
            report = {'profile': profile, 'retained_trials': ['fixture'], 'final': {'answer': FINAL_SENTINEL},
                      'runtime': {'opencode': {'binary_sha256': OPENCODE_LINUX_SHA256}}}
            if profile == 'zvec-grep':
                report['zg_setup'] = {'status': 'ready', 'install_reused_cache': False,
                                     'index_cache_hit': False, 'authorization_scope': 'workspace'}
                report['runtime']['zg'] = {'payloads': {'bin/zg': {'sha256': 'a' * 64, 'bytes': 10, 'mode': '0755'}}}
            collection['profiles'].append(report)
        return collection, root / 'run', manifest, prepared

    def test_proof_checks_real_usage_final_identity_and_installed_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            collection, run, manifest, prepared = self.make_proof(Path(temp))
            result = validate_native_proof(collection, run, Services(), manifest, prepared)
            self.assertTrue(result['valid'])
            for field, value in [('structural_valid', False)]:
                bad = copy.deepcopy(collection)
                bad[field] = value
                with self.assertRaises(ValueError):
                    validate_native_proof(bad, run, Services(), manifest, prepared)
            bad = copy.deepcopy(collection)
            bad['profiles'][0]['final']['answer'] = 'partial'
            with self.assertRaisesRegex(ValueError, 'sentinel'):
                validate_native_proof(bad, run, Services(), manifest, prepared)
            bad = copy.deepcopy(collection)
            bad['profiles'][1]['runtime']['zg']['payloads']['bin/zg']['sha256'] = 'b' * 64
            with self.assertRaisesRegex(ValueError, 'runtime payload'):
                validate_native_proof(bad, run, Services(), manifest, prepared)
            usage_file = run / 'runs/q001-baseline/fixture/agent/session-usage.json'
            usage = pilot.load(usage_file)
            usage['total']['llm_calls'] = 99
            pilot.write(usage_file, usage)
            with self.assertRaisesRegex(ValueError, 'usage mismatch'):
                validate_native_proof(collection, run, Services(), manifest, prepared)

    def test_actual_native_lexical_and_entity_renderings_are_source_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, _, prepared = self.make_proof(Path(temp))
            for marker in ('', ':', '-'):
                output = 'freshness: fresh\n#1 [global_fill] matchedBy=fts README.md:1-2\n1' + marker + '\tfirst source line'
                self.assertEqual(verify_mcp_source(output, prepared), {'path': 'README.md', 'line': 1})
            output = 'freshness: fresh\n#1 matchedBy=vector README.md:1-2\noutline only\n#2 matchedBy=fts README.md:1-2\n2:\tsecond source line'
            self.assertEqual(verify_mcp_source(output, prepared), {'path': 'README.md', 'line': 2})

    def test_successful_embedding_does_not_excuse_failed_or_empty_mcp(self):
        with tempfile.TemporaryDirectory() as temp:
            collection, run, manifest, prepared = self.make_proof(Path(temp))
            export_path = run / 'runs/q001-zvec-grep/fixture/qa-session-exports/zvec-grep_root.json'
            original = pilot.load(export_path)
            for state, output in [('error', 'failure'), ('completed', 'freshness: fresh\nNo matches.'),
                                  ('completed', 'freshness: fresh\n#1 matchedBy=vector README.md:1-2\nsource:\n1\twrong content')]:
                bad = copy.deepcopy(original)
                bad['messages'][0]['parts'][-1]['state'].update(status=state, output=output)
                pilot.write(export_path, bad)
                with self.subTest(state=state, output=output), self.assertRaises(ValueError):
                    validate_native_proof(collection, run, Services(), manifest, prepared)


if __name__ == '__main__':
    unittest.main()
