"""Seal each pair of independent supplemental reviews before parent reads.

This does not read assignments, display judgments, or alter either review.
"""
from pathlib import Path
import datetime,hashlib,json
B=Path(__file__).resolve().parent.parent
G=B/'native-five-completion-v2-grading'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    root=G/'seals';root.mkdir(parents=True,exist_ok=True);added=[]
    for manifest in sorted(G.glob('q*/manifest.json')):
        task=manifest.parent.name
        reports=[G/role/task/'report.json' for role in ('primary','secondary')]
        if not all(p.is_file() for p in reports):continue
        try:
            for p in reports:json.loads(p.read_text())
        except json.JSONDecodeError:continue
        files=[p for report in reports for p in sorted(report.parent.glob('*.json'))]
        records=[{'path':str(p),'sha256':sha(p),'bytes':p.stat().st_size} for p in files]
        value={'schema_version':1,'task_id':task,'manifest_sha256':sha(manifest),
            'reports_and_ancillary_files':records,
            'sealed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'this_call_read_for_hashes_only':True,'this_call_printed_judgments':False,
            'parent_condition_mapping_read':False,'method_sha256':sha(Path(__file__))}
        out=root/(task+'-independent-report-byte-seal.json')
        if out.exists():
            old=json.loads(out.read_text());assert old['manifest_sha256']==value['manifest_sha256']
            for record in old['reports_and_ancillary_files']:
                assert sha(Path(record['path']))==record['sha256']
            continue
        with out.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
        added.append({'task_id':task,'seal_sha256':sha(out)})
    print(json.dumps({'new_report_byte_seals':added,'condition_mapping_read':False}))
if __name__=='__main__':main()
