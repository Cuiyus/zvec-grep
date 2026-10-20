from pathlib import Path
from tempfile import TemporaryDirectory
import copy
import unittest
import recovery


class ZeroStartAdmissionTests(unittest.TestCase):
    def fixture(self):
        return {'agent_setup':None,'agent_execution':None,'agent_result':None,
                'exception_info':{'exception_type':'RuntimeError','exception_message':"Docker compose command failed. Command: docker compose build. Could not resolve 'archive.ubuntu.com'"}}

    def test_exact_pre_agent_DNS_case_is_eligible(self):
        with TemporaryDirectory() as name:recovery.eligible(self.fixture(),Path(name))

    def test_any_started_agent_or_persisted_evidence_forbids_completion(self):
        with TemporaryDirectory() as name:
            root=Path(name)
            for key in ('agent_setup','agent_execution','agent_result'):
                value=self.fixture();value[key]={}
                with self.assertRaisesRegex(ValueError,'started'):recovery.eligible(value,root)
            (root/'agent').mkdir();(root/'agent/opencode.txt').touch()
            with self.assertRaisesRegex(ValueError,'evidence'):recovery.eligible(self.fixture(),root)

    def test_model_or_generic_setup_failure_is_not_a_zero_start_recovery(self):
        with TemporaryDirectory() as name:
            item=self.fixture();item['exception_info']['exception_message']='API rate limit after model invocation'
            with self.assertRaisesRegex(ValueError,'only_proven'):recovery.eligible(item,Path(name))


if __name__=='__main__':unittest.main()
