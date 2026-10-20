"""Bind a current Rust build to the preserved native harness and common context.

Historical plans, payload pins and proofs remain unchanged. New source pins
must come from separately retained API-bound build artifacts. The old package
writer, native execution bodies and collector bodies are loaded privately with
explicit replacement payload identities; their historical module globals are
never mutated. This module itself authorizes no live model experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

import workspace_context_v1 as workspace

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
SOURCE_COMMIT='ae1ac57d489542508e016bba5d868bc38c3c095a'
RUST_TREE='577d1fd05902ba844bc9c85e2cc58d64f425252e'
ORIGINALS={
    'package':('benchmarks/opencode-qa-pilot/repair_v2/package.py','6985cbaec95a10bcb6aece75fdc91d8ed6a8ece34efd6ce88ce3dee9eef20edc'),
    'integration':('benchmarks/native-claude-qa-pilot/native_claude/integration.py','669ac033aac53d1bc5bb49d52b93cbb336fcd734acd39db95919c05bed9c59a5'),
    'live':('benchmarks/native-claude-qa-pilot/live.py','6f625a7ee0c090063f295c780f2eefb275f5fa1a29d8137fcf4f076a6ee1906b'),
}

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()

def load(path):return json.loads(Path(path).read_text())
def write(path,data):
    with Path(path).open('x') as f:json.dump(data,f,indent=2);f.write('\n')
def is_sha(value):return isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value) is not None

def original(kind):
    relative,expected=ORIGINALS[kind];path=REPO/relative
    if digest(path)!=expected:raise ValueError('original_harness_changed:'+kind)
    name='native_claude._current_candidate_integration' if kind=='integration' else '_current_candidate_'+kind
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def validate_source_pins(pins):
    if type(pins.get('schema_version')) is not int or pins['schema_version']!=1 or pins.get('source')!={
        'repository':'zvec-ai/zvec-grep','commit':SOURCE_COMMIT,'rust_tree':RUST_TREE,'target':'linux-x64-gnu'}:
        raise ValueError('current_source_identity_required')
    build=pins.get('build',{})
    if (build.get('repository')!='Cuiyus/zvec-grep' or type(build.get('run_attempt')) is not int or build['run_attempt']!=1
        or type(build.get('run_id')) is not int or type(build.get('artifact_id')) is not int
        or not is_sha(build.get('artifact_sha256')) or re.fullmatch('[a-f0-9]{40}',str(build.get('head_sha',''))) is None):
        raise ValueError('fork_build_artifact_identity_required')
    archive=pins.get('upstream_archive',{})
    if not is_sha(archive.get('sha256')) or type(archive.get('bytes')) is not int or archive['bytes']<=0:
        raise ValueError('source_archive_identity_required')
    package=original('package');members=pins.get('members')
    if not isinstance(members,dict) or set(members)!=set(package.EXPECTED_MEMBERS):
        raise ValueError('all_seven_platform_members_required')
    for name,entry in members.items():
        if (not isinstance(entry,list) or len(entry)!=3 or type(entry[0]) is not int or entry[0]<0
            or not is_sha(entry[1]) or type(entry[2]) is not int or entry[2]!=package.EXPECTED_MEMBERS[name][2]):
            raise ValueError('platform_member_identity_invalid:'+name)
    if members['package/bin/zg'][1]==package.BINARY_SHA256:
        raise ValueError('historical_binary_is_not_the_current_build')
    return pins

def read_source_pins(path,expected_sha):
    if not is_sha(expected_sha) or digest(path)!=expected_sha:raise ValueError('source_pins_changed')
    return validate_source_pins(load(path))

def derive(source,source_pins,source_pins_sha,output,derivation,manifest_path):
    pins=read_source_pins(source_pins,source_pins_sha)
    if Path(manifest_path).exists():raise FileExistsError(manifest_path)
    module=original('package')
    module.UPSTREAM_COMMIT=SOURCE_COMMIT
    module.UPSTREAM_ARCHIVE_SHA256=pins['upstream_archive']['sha256']
    module.UPSTREAM_ARCHIVE_BYTES=pins['upstream_archive']['bytes']
    module.BINARY_SHA256=pins['members']['package/bin/zg'][1]
    module.EXPECTED_MEMBERS={k:tuple(v) for k,v in pins['members'].items()}
    result=module.derive_package(Path(source),Path(output),Path(derivation))
    if not all(row['unchanged'] for row in result['runtime_payloads']):raise ValueError('runtime_payload_changed')
    manifest={'schema_version':1,'source_pins':pins,'source_pins_sha256':source_pins_sha,
        'install_archive':{'sha256':result['install_package_sha256'],'bytes':result['install_package_bytes']},
        'derivation_sha256':digest(derivation),'package_adapter_sha256':digest(__file__),
        'runtime_payloads_unchanged':True,'original_package_writer_sha256':ORIGINALS['package'][1],
        'workspace_context_sha256':workspace.CONTEXT_SHA256,'live_experiment_authorized':False}
    write(manifest_path,manifest)
    return manifest

def validate_candidate(manifest):
    if manifest.get('schema_version')!=1:raise ValueError('candidate_manifest_version')
    validate_source_pins(manifest['source_pins'])
    if (not manifest.get('runtime_payloads_unchanged') or not is_sha(manifest.get('source_pins_sha256'))
        or not is_sha(manifest.get('derivation_sha256'))
        or manifest.get('package_adapter_sha256')!=digest(__file__)
        or manifest.get('original_package_writer_sha256')!=ORIGINALS['package'][1]
        or manifest.get('workspace_context_sha256')!=workspace.CONTEXT_SHA256):
        raise ValueError('candidate_derivation_or_context_identity')
    archive=manifest.get('install_archive',{})
    if not is_sha(archive.get('sha256')) or type(archive.get('bytes')) is not int or archive['bytes']<=0:
        raise ValueError('candidate_archive_identity')
    return manifest

def read_candidate(path,expected_sha):
    if not is_sha(expected_sha) or digest(path)!=expected_sha:raise ValueError('candidate_manifest_changed')
    return validate_candidate(load(path))

def native_module(manifest,kind,*,protocol=None,profiles=None):
    validate_candidate(manifest)
    if kind not in ('integration','live'):raise ValueError('native_module_kind')
    module=original(kind)
    module.PACKAGE_SHA=manifest['install_archive']['sha256']
    module.EXPECTED_MEMBERS={k:tuple(v) for k,v in manifest['source_pins']['members'].items()}
    original_builder=module.build_command
    def build_command(*args,**kwargs):
        return workspace.append_context(original_builder(*args,**kwargs))
    module.build_command=build_command
    if kind=='live':
        if not isinstance(protocol,str) or not protocol or tuple(profiles or ()) not in (
            ('baseline',),('zvec-grep',),('baseline','zvec-grep')):
            raise ValueError('explicit_live_protocol_and_profile_set_required')
        module.PROTOCOL=protocol;module.PROFILES=list(profiles)
    return module

def offline(args):
    manifest=read_candidate(args.manifest,args.manifest_sha256)
    if (digest(args.candidate)!=manifest['install_archive']['sha256']
        or args.candidate.stat().st_size!=manifest['install_archive']['bytes']):raise ValueError('candidate_package_changed')
    module=native_module(manifest,'integration')
    status=module.main(['--repo-root',str(REPO),'--main-root',str(args.main_root),
        '--prepared',str(args.prepared),'--candidate-tgz',str(args.candidate),'--output',str(args.output)])
    proof=load(args.output/'proof.json');context_checks=[]
    def text(node):
        if isinstance(node,str):return node
        if isinstance(node,list):return '\n'.join(text(x) for x in node)
        if isinstance(node,dict):return '\n'.join(text(x) for x in node.values())
        return ''
    for profile in ('baseline','zvec-grep'):
        trace=load(args.output/(profile+'-provider-trace.json'))
        roots=[r for r in trace['rows'] if r.get('kind')=='chat' and r.get('classification')=='root']
        count=text(roots[0]['body'].get('system')).count(workspace.CONTEXT) if roots else 0
        context_checks.append({'profile':profile,'first_actual_root_system_context_occurrences':count,'passed':count==1})
    result={'schema_version':1,'protocol_id':'native-claude-current-rust-workspace-offline-v1',
        'manifest_sha256':args.manifest_sha256,'source_commit':SOURCE_COMMIT,'rust_tree':RUST_TREE,
        'candidate_sha256':manifest['install_archive']['sha256'],'workspace_context_sha256':workspace.CONTEXT_SHA256,
        'original_native_proof_sha256':digest(args.output/'proof.json'),'original_native_checks_passed':status==0 and proof['valid'],
        'actual_provider_context_checks':context_checks,'valid':status==0 and proof['valid'] and all(r['passed'] for r in context_checks),
        'live_providers_called':False,'qa_quality_evaluated':False,'original_proof_and_inputs_left_unchanged':True,
        'adapter_sha256':digest(__file__)}
    write(args.output/'current-candidate-binding.json',result)
    print(json.dumps(result))
    return 0 if result['valid'] else 1

def main():
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='command',required=True)
    derive_parser=subs.add_parser('derive')
    for name in ('source','source-pins','output','derivation','candidate-manifest'):derive_parser.add_argument('--'+name,type=Path,required=True)
    derive_parser.add_argument('--source-pins-sha256',required=True)
    probe=subs.add_parser('offline')
    for name in ('manifest','candidate','main-root','prepared','output'):probe.add_argument('--'+name,type=Path,required=True)
    probe.add_argument('--manifest-sha256',required=True)
    args=parser.parse_args()
    if args.command=='offline':return offline(args)
    result=derive(args.source,args.source_pins,args.source_pins_sha256,args.output,args.derivation,args.candidate_manifest)
    print(json.dumps({'install_archive':result['install_archive'],'candidate_manifest_sha256':digest(args.candidate_manifest)}));return 0

if __name__=='__main__':raise SystemExit(main())
