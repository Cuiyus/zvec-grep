"""Protect source/current-payload bindings and the frozen native function bodies."""
import copy
from pathlib import Path
import tempfile
import unittest

import live
from native_claude import integration
from repair_v2 import package
import rust_candidate_v1 as candidate
import workspace_context_v1 as workspace


class CurrentCandidateTests(unittest.TestCase):
    def manifest(self):
        members={k:list(v) for k,v in package.EXPECTED_MEMBERS.items()}
        # Synthetic changed binary identity; this fixture is never an actual
        # current-source build, install package, offline proof or QA result.
        members['package/bin/zg'][1]='a'*64
        pins={'schema_version':1,'source':{'repository':'zvec-ai/zvec-grep',
            'commit':candidate.SOURCE_COMMIT,'rust_tree':candidate.RUST_TREE,'target':'linux-x64-gnu'},
            'build':{'repository':'Cuiyus/zvec-grep','head_sha':'1'*40,
                'run_id':1,'run_attempt':1,'artifact_id':2,'artifact_sha256':'c'*64},
            'upstream_archive':{'bytes':123,'sha256':'d'*64},'members':members}
        return {'schema_version':1,'source_pins':pins,'source_pins_sha256':'e'*64,
            'install_archive':{'sha256':'b'*64,'bytes':456},'derivation_sha256':'f'*64,
            'runtime_payloads_unchanged':True,'original_package_writer_sha256':candidate.ORIGINALS['package'][1],
            'package_adapter_sha256':candidate.digest(candidate.__file__),
            'workspace_context_sha256':workspace.CONTEXT_SHA256}

    def test_historical_binary_other_source_or_upstream_build_rejected(self):
        for field,value in [('binary',package.BINARY_SHA256),('commit','0'*40),
                            ('repository','zvec-ai/zvec-grep')]:
            with self.subTest(field=field):
                m=self.manifest()
                if field=='binary':m['source_pins']['members']['package/bin/zg'][1]=value
                elif field=='commit':m['source_pins']['source']['commit']=value
                else:m['source_pins']['build']['repository']=value
                with self.assertRaises(ValueError):candidate.validate_candidate(m)

    def test_all_payload_members_modes_adapter_and_context_remain_bound(self):
        for field in ('member','mode','adapter','context'):
            with self.subTest(field=field):
                m=self.manifest()
                if field=='member':del m['source_pins']['members']['package/bin/libzvec_c_api.so']
                if field=='mode':m['source_pins']['members']['package/bin/zg'][2]=0o644
                if field=='adapter':m['package_adapter_sha256']='0'*64
                if field=='context':m['workspace_context_sha256']='0'*64
                with self.assertRaises(ValueError):candidate.validate_candidate(m)

    def test_private_payload_parameters_preserve_live_and_offline_bodies_and_globals(self):
        old=(live.PACKAGE_SHA,copy.deepcopy(live.EXPECTED_MEMBERS),live.PROTOCOL,list(live.PROFILES),
             integration.PACKAGE_SHA,copy.deepcopy(integration.EXPECTED_MEMBERS))
        m=self.manifest()
        current_live=candidate.native_module(m,'live',protocol='synthetic-protocol',profiles=('baseline','zvec-grep'))
        current_offline=candidate.native_module(m,'integration')
        for current,original,names in ((current_live,live,('run_slot','run_pair','collect')),
                                       (current_offline,integration,('run_slot','verify_slot'))):
            for name in names:
                self.assertEqual(getattr(current,name).__code__.co_code,getattr(original,name).__code__.co_code)
            self.assertEqual(current.PACKAGE_SHA,'b'*64)
            self.assertEqual(current.EXPECTED_MEMBERS['package/bin/zg'][1],'a'*64)
        self.assertEqual(old,(live.PACKAGE_SHA,live.EXPECTED_MEMBERS,live.PROTOCOL,live.PROFILES,
                             integration.PACKAGE_SHA,integration.EXPECTED_MEMBERS))

    def test_manifest_file_hash_checked_before_loading(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'manifest.json';candidate.write(path,self.manifest())
            expected=candidate.digest(path)
            self.assertEqual(candidate.read_candidate(path,expected)['install_archive']['sha256'],'b'*64)
            path.write_bytes(path.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError,'manifest_changed'):candidate.read_candidate(path,expected)


if __name__=='__main__':unittest.main()
