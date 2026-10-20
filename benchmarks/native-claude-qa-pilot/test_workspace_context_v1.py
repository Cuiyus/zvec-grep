"""Exercise exact native Harbor/Claude flag transport and differential parity."""
import shlex
import tempfile
from pathlib import Path
import unittest

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.cli.utils import parse_kwargs

import workspace_context_v1 as context


class WorkspaceContextTests(unittest.TestCase):
    def command(self, profile):
        return ['harbor', 'run', '--agent', 'native_claude.agents:' + (
            'QABaseline' if profile == 'baseline' else 'QAZvec'),
            '--model', 'qwen3.8-max', '--agent-kwarg', 'version=2.1.212',
            '--agent-kwarg', 'reasoning_effort=high', '--agent-kwarg',
            'max_budget_usd=4.0', '--agent-kwarg', 'qa_profile=' + profile]

    def test_same_append_and_unchanged_original_argv_for_both_profiles(self):
        for profile in ('baseline', 'zvec-grep'):
            original=self.command(profile);saved=list(original)
            actual=context.append_context(original)
            self.assertEqual(original,saved)
            self.assertEqual(actual[:-2],original)
            self.assertEqual(actual[-2:],['--agent-kwarg',context.KWARG])
            self.assertTrue(context.verify_context_command(original,actual)['original_arguments_unchanged'])

    def test_real_harbor_parser_and_claude_cli_transport_exact_text(self):
        parsed=parse_kwargs([context.KWARG])
        self.assertEqual(parsed,{'append_system_prompt':shlex.quote(context.CONTEXT)})
        with tempfile.TemporaryDirectory() as temp:
            agent=ClaudeCode(logs_dir=Path(temp),model_name='qwen3.8-max',
                reasoning_effort='high',max_budget_usd=4.0,**parsed)
            flags=shlex.split(agent.build_cli_flags())
        self.assertEqual(flags.count('--append-system-prompt'),1)
        self.assertEqual(flags[flags.index('--append-system-prompt')+1],context.CONTEXT)
        self.assertEqual(flags[flags.index('--effort')+1],'high')
        self.assertEqual(flags[flags.index('--max-budget-usd')+1],'4.0')

    def test_duplicate_context_and_any_other_command_change_rejected(self):
        original=self.command('baseline');actual=context.append_context(original)
        with self.assertRaisesRegex(ValueError,'duplicated'):
            context.append_context(actual)
        for replacement in ('qwen-other','native_claude.agents:QAZvec'):
            changed=list(actual);changed[changed.index('--model')+1]=replacement
            with self.assertRaisesRegex(ValueError,'beyond_shared'):
                context.verify_context_command(original,changed)
        with self.assertRaisesRegex(ValueError,'native_agent_and_model'):
            context.append_context(['unrelated-command'])


if __name__=='__main__':unittest.main()
