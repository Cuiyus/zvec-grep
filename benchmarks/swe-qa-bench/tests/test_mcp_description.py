"""Require real list use and multi-group tool evidence; reject scalar-only probes."""
import copy
import json
import unittest
from zg_bench.mcp_description_e2e import selected_tasks, SMOKE_TASKS
from zg_bench.mcp_description_probe import validate, PROBES, proxy_source


class DescriptionExperimentTests(unittest.TestCase):
    def evidence(self, field):
        description = 'Do not JSON-stringify arrays.'
        schema = {'properties':{f:{'description':description} for f in PROBES}}
        request = {'model':'qwen3.8-max','tools':[{'function':{
            'name':'zvec_grep_zvec_grep_search','description':description,'parameters':schema}}]}
        tool = {'name':'zvec_grep_zvec_grep_search','status':'completed',
                'input':{field:['one','two']},'output':'query groups (2):\n#1 file.py:1\n'}
        return [{'kind':'tool','data':tool}], [request]

    def test_all_fields_require_real_arrays_and_multiple_groups(self):
        for field in PROBES:
            snapshot, requests = self.evidence(field)
            self.assertTrue(validate(field,snapshot,requests)['passed'])
            for bad in ('one', '["one","two"]', ['one']):
                changed = copy.deepcopy(snapshot)
                changed[0]['data']['input'][field] = bad
                with self.assertRaises(AssertionError): validate(field,changed,requests)
            changed = copy.deepcopy(snapshot)
            changed[0]['data']['output'] = 'No matches.'
            with self.assertRaises(AssertionError): validate(field,changed,requests)
            with self.assertRaises(AssertionError): validate(field,snapshot,[])

    def test_formal_budget_and_original_smoke_questions(self):
        formal = selected_tasks('formal')
        self.assertEqual(len(formal),20)
        self.assertTrue(set(SMOKE_TASKS) <= set(formal))
        self.assertEqual(len(selected_tasks('smoke')),3)
        self.assertEqual(20*10*3,600)

    def test_recorder_excludes_message_text_and_reasoning(self):
        source = proxy_source('https://example.test/v1')
        self.assertNotIn('body.messages',source)
        self.assertNotIn('reasoning_content',source)
        self.assertNotIn('save(\'headers',source)

if __name__ == '__main__': unittest.main()
