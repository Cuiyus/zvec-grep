"""Preparation-only tests: synthetic identity/capture checks and frozen local inputs."""
from pathlib import Path
import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent
REPO = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
MAIN = BASE / 'upstream-main/benchmarks/swe-qa-bench'
spec = importlib.util.spec_from_file_location('confirmation_source_audit', ROOT / 'audit.py')
audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)

class PreparationTests(unittest.TestCase):
    def valid_identity(self, repeat='c01'):
        return {'protocol_id': audit.PROTOCOL, 'phase': audit.PHASE, 'repeat': repeat,
            'plan_sha256': audit.PLAN_SHA, 'parent_development_plan_sha256': audit.PARENT_PLAN_SHA}

    def test_repeat_identities(self):
        for repeat in audit.REPEATS:
            self.assertEqual(audit.identity(self.valid_identity(repeat), repeat)['repeat'], repeat)

    def test_cross_repeat_rejected(self):
        with self.assertRaises(ValueError): audit.identity(self.valid_identity('c01'), 'c02')

    def test_wrong_protocol_plan_phase_and_parent_rejected(self):
        for key in ('protocol_id','phase','plan_sha256','parent_development_plan_sha256'):
            value = self.valid_identity(); value[key] = 'wrong'
            with self.assertRaises(ValueError): audit.identity(value,'c01')

    def test_missing_identity_rejected(self):
        for key in self.valid_identity():
            value = self.valid_identity(); del value[key]
            with self.assertRaises(ValueError): audit.identity(value,'c01')

    def test_prior_run_cannot_be_relabelled(self):
        with self.assertRaisesRegex(ValueError, 'wrong confirmation run'):
            audit.retained_binding(BASE / 'live-v2-ci', 'c01')

    def test_exact_frozen_confirmation_inputs(self):
        plan_path = REPO / 'benchmarks/opencode-qa-pilot/confirmation-v1.json'
        self.assertEqual(audit.sha(plan_path),audit.PLAN_SHA)
        plan=audit.load(plan_path)
        for rel,digest in plan['frozen_harness_sha256'].items(): self.assertEqual(audit.sha(REPO/rel),digest,rel)
        for rel,digest in plan['main_file_sha256'].items(): self.assertEqual(audit.sha(MAIN/rel),digest,rel)
        parent=audit.load(REPO/plan['parent_development_plan'])
        for key in ('accounting','evaluation','agent','provider','embedding','runtime','isolation'):
            self.assertEqual(plan[key],parent[key])

    def test_both_prepared_task_algorithms(self):
        plan=audit.load(REPO/'benchmarks/opencode-qa-pilot/confirmation-v1.json')
        hashes=[]
        for repeat in audit.REPEATS:
            source=BASE/'confirmation-v1-local'/repeat/'prepared/tasks/zvec-q001/environment/source'
            h=audit.task_identity(source,REPO,plan); hashes.append(h)
            self.assertEqual(h['lock_digest'],'sha256:59bd7cc5f70b054d1305954fa720636dc6230003acf4826d39be13f1a44f90e9')
            self.assertEqual(h['legacy_checksum'],'ff1810e432e6de5d6b111ee6e02f0fb55d131d88201d6adbcb30113d72cd6e9a')
            self.assertEqual(h['total_files'],1236)
        self.assertEqual(hashes[0],hashes[1])

    def test_valid_and_modified_zip_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp); raw=tmp/'raw';raw.mkdir();(raw/'file').write_bytes(b'x')
            with zipfile.ZipFile(tmp/'artifact.zip','w') as z:z.writestr('file',b'x')
            digest=audit.sha(tmp/'artifact.zip')
            self.assertEqual(len(audit.verify_zip(tmp/'artifact.zip',raw,digest)['files']),1)
            (raw/'file').write_bytes(b'y')
            with self.assertRaises(ValueError):audit.verify_zip(tmp/'artifact.zip',raw,digest)

    def test_unsafe_zip_rejected(self):
        for name in ('../file','/file','x/../file','x//file','x\\file'):
            with tempfile.TemporaryDirectory() as tmp:
                tmp=Path(tmp);raw=tmp/'raw';raw.mkdir()
                with zipfile.ZipFile(tmp/'artifact.zip','w') as z:z.writestr(name,b'x')
                with self.assertRaises(ValueError):audit.verify_zip(tmp/'artifact.zip',raw,audit.sha(tmp/'artifact.zip'))

    def test_missing_evidence_report_preserved_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);out=tmp/'report'
            args=['--evidence-root',str(tmp/'unavailable'),'--repo-root',str(REPO),'--main-root',str(MAIN),
                '--source-root',str(tmp/'source'),'--repeat','c01','--output',str(out)]
            with contextlib.redirect_stdout(io.StringIO()):rc=audit.main(args)
            self.assertEqual(rc,1)
            result=audit.load(out/'audit.json')
            self.assertFalse(result['protocol_valid']);self.assertTrue(result['errors'])
            self.assertEqual(result['run_id'],audit.EXPECTED_RUN)
            before=(out/'audit.json').read_bytes()
            with self.assertRaises(ValueError):audit.main(args)
            self.assertEqual(before,(out/'audit.json').read_bytes())

if __name__=='__main__': unittest.main(verbosity=2)
