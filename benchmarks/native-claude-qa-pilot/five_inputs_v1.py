"""Prepare the accepted five questions using their unchanged native adapters."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import expansion_v1 as reserves
import repeat_inputs_v1 as first_three
import architecture_preparation_v1 as architecture
import runtime_preparation_v1 as runtime

TASK_IDS=('q001','q002','q003','q004','q005')
REPO=reserves.REPO
pilot=reserves.pilot

def prepare(reference_prepared,zvec_repo,output,repo_root=REPO):
    output.mkdir(parents=True,exist_ok=False)
    reserves.prepare(reference_prepared,zvec_repo,output/'reserves',repo_root)
    architecture.prepare(reference_prepared,zvec_repo,output/'architecture',repo_root)
    runtime.prepare(reference_prepared,zvec_repo,output/'runtime',repo_root)
    record={'schema_version':1,'task_ids':list(TASK_IDS),'groups':{
        name:pilot.digest(output/name/'group.json') for name in ('reserves','architecture','runtime')},
        'reference_metadata_sha256':pilot.digest(reference_prepared/'prepared.json'),
        'question_bank_expanded':False}
    with (output/'group.json').open('x') as f:json.dump(record,f,indent=2);f.write('\n')
    return validate(output,reference_prepared,repo_root)

def validate(group,reference_prepared,repo_root=REPO):
    reserves.validate(group/'reserves',reference_prepared,repo_root)
    architecture.validate(group/'architecture',reference_prepared,repo_root)
    runtime.validate(group/'runtime',reference_prepared,repo_root)
    actual=pilot.load(group/'group.json')
    expected={'schema_version':1,'task_ids':list(TASK_IDS),'groups':{
        name:pilot.digest(group/name/'group.json') for name in ('reserves','architecture','runtime')},
        'reference_metadata_sha256':pilot.digest(reference_prepared/'prepared.json'),
        'question_bank_expanded':False}
    if actual!=expected:raise ValueError('fixed_five_task_group_changed')
    return actual

def task_root(group,reference_prepared,task_id):
    if task_id not in TASK_IDS:raise ValueError('only_five_existing_tasks_allowed')
    if task_id=='q001':return reference_prepared
    name='reserves' if task_id in ('q002','q003') else 'architecture' if task_id=='q004' else 'runtime'
    return group/name/task_id

def _selected_after_validation(group,reference_prepared,task_id,repo_root):
    if task_id=='q001':
        return first_three.selected_inputs(group/'reserves',reference_prepared,task_id,repo_root)
    root=task_root(group,reference_prepared,task_id)
    return pilot.load(root/'prepared.json'),pilot.load(root/'source-manifest.json')['files'],root

def selected_inputs(group,reference_prepared,task_id,repo_root=REPO):
    validate(group,reference_prepared,repo_root)
    return _selected_after_validation(group,reference_prepared,task_id,repo_root)

def references(group,reference_prepared,repo_root=REPO):
    validate(group,reference_prepared,repo_root)
    result={'group_sha256':pilot.digest(group/'group.json'),'tasks':{}}
    for task in TASK_IDS:
        metadata,_,root=_selected_after_validation(group,reference_prepared,task,repo_root)
        result['tasks'][task]={'metadata_sha256':pilot.digest(root/'prepared.json'),
            'source_manifest_sha256':pilot.digest(root/'source-manifest.json'),
            'source_commit':metadata['source_commit'],'source_inventory_sha256':metadata['source_inventory_sha256'],
            'question':metadata['question'],'rubric':metadata['rubric'],'native_task_hashes':metadata['native_task_hashes']}
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('prepare','validate'))
    for name in ('reference-prepared','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--zvec-repo',type=Path);a=p.parse_args()
    if a.command=='prepare':
        if a.zvec_repo is None:p.error('--zvec-repo required')
        prepare(a.reference_prepared,a.zvec_repo,a.output)
    refs=references(a.output,a.reference_prepared)
    print(json.dumps(refs))

if __name__=='__main__':main()
