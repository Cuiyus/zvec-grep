from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qoder_probe


class QoderProbeTests(unittest.TestCase):
    def run_probe(self, directory, *, bridge_ok=True, metrics_ok=True, model_ok=True, launch_error=False):
        root = Path(directory)
        source, output, cache, index = [root / name for name in ('source', 'qoder', 'cache', 'index')]
        for path in (source, cache, index):
            path.mkdir(exist_ok=True)
        calls = []
        def run(command, name, **kwargs):
            calls.append(command)
            if name.endswith('-preflight'):
                (output / 'snapshot.json').write_text('{}')
                return ''
            if launch_error:
                raise RuntimeError('fixture-embedding-key unavailable')
            agent = output / 'agent'
            (agent / 'session.json').write_text(json.dumps({'status': 'completed'}))
            event = {'event': 'search', 'origin': 'agent-mcp', 'status': 'success' if bridge_ok else 'error',
                     'request': {'routes': [{'mode': 'vector', 'query': 'repository source files'}]},
                     'text': 'probe.md:1 repository source files' if bridge_ok else ''}
            (agent / 'zg-trace.jsonl').write_text(json.dumps(event) + '\n')
            return ''
        conversion = {'has_final_answer': True, 'contract_error_count': 0, 'error_event_count': 0,
                      'model_identity': {'valid': model_ok, 'observed': ['qwen3.8-max']}}
        metrics = {'input_tokens': 123 if metrics_ok else None, 'tool_calls': 1,
                   'zg_tool_calls_successful': 1 if bridge_ok else 0}
        with patch.dict(os.environ, {'QWEN_API_KEY': 'fixture-embedding-key',
                                     'QODER_PERSONAL_ACCESS_TOKEN': 'fixture-qoder-key'}), \
                patch.object(qoder_probe.runner, 'run_named', side_effect=run), \
                patch.object(qoder_probe.runner, 'convert_agent_trace', return_value=conversion), \
                patch.object(qoder_probe.runner, 'trial_metrics', return_value=metrics):
            qoder_probe.qoder_mcp_preflight(source, output, cache, index)
        return output, calls

    def test_real_agent_launch_contract_uses_named_credentials_and_records_setup_only(self):
        with tempfile.TemporaryDirectory() as directory:
            output, calls = self.run_probe(directory)
            report = json.loads((output / 'result.json').read_text())
            self.assertEqual(report['status'], 'completed')
            self.assertFalse(report['included_in_benchmark'])
            self.assertEqual(report['successful_vector_searches'], 1)
            self.assertIn('QWEN_API_KEY', calls[-1])
            self.assertIn('QODER_PERSONAL_ACCESS_TOKEN', calls[-1])
            spec = json.loads((output / 'agent/session-spec.json').read_text())
            self.assertEqual(spec['limits']['wall_seconds'], 120)
            self.assertEqual(spec['limits']['model_requests'], 4)
            self.assertIn('Qwen3.8-Max', spec['command'])
            config = json.loads((output / qoder_probe.runner.SPEC.config_filename).read_text())
            self.assertEqual(config['mcpServers']['zvec_grep']['env']['QWEN_API_KEY'], '${QWEN_API_KEY}')
            for path in output.rglob('*'):
                if path.is_file():
                    self.assertNotIn('fixture-embedding-key', path.read_text())
                    self.assertNotIn('fixture-qoder-key', path.read_text())
            self.assertNotIn('fixture-embedding-key', json.dumps(calls))

    def test_terminal_success_cannot_hide_a_failed_mcp_search(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'successful remote vector search'):
                self.run_probe(directory, bridge_ok=False)
            report = json.loads((Path(directory) / 'qoder/result.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['successful_vector_searches'], 0)

    def test_missing_native_usage_or_wrong_model_fails_despite_a_vector_result(self):
        for options in ({'metrics_ok': False}, {'model_ok': False}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(RuntimeError):
                    self.run_probe(directory, **options)
                report = json.loads((Path(directory) / 'qoder/result.json').read_text())
                self.assertEqual(report['status'], 'failed')

    def test_launch_failure_retains_redacted_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                self.run_probe(directory, launch_error=True)
            text = (Path(directory) / 'qoder/result.json').read_text()
            self.assertNotIn('fixture-embedding-key', text)
            self.assertIn('[REDACTED]', text)


if __name__ == '__main__':
    unittest.main()
