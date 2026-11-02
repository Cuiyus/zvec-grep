from pathlib import Path
import tempfile
import unittest

from zg_bench.case_audit import parse_hits


class CaseAuditParsingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name)

    def write_source(self, text):
        (self.source / "example.py").write_text(text)

    def test_legacy_and_new_unicode_preview_preserve_exact_source(self):
        self.write_source('name = "注解…"\nnext_value = 1\n')
        legacy = '#1 matchedBy=hybrid example.py:1-2\nsource:\n1\tname = "注解…"\n2\tnext_value = 1\n'
        compact = '#1 example.py:1-2\nsymbol: [function] example\n  1: name = "注解…"\n  2: next_value = 1\n  …\n'
        for body in (legacy, compact):
            with self.subTest(body=body):
                hit, = parse_hits(body, self.source)
                self.assertEqual(hit["path"], "example.py")
                self.assertEqual(hit["source_lines_checked"], 2)
                self.assertEqual(hit["source_mismatches"], [])
                self.assertEqual(hit["source_display_differences"], [])
                self.assertEqual(hit["text"], body)

    def test_query_groups_preserve_repeated_rank_and_exclude_next_header(self):
        self.write_source("a = 1\nb = 2\n")
        body = ("freshness: fresh\n-- query 1/2: first query --\n\n"
                "#1 example.py:1-1\n  1: a = 1\n\n"
                "-- query 2/2: second query --\n\n#1 example.py:2-2\n  2: b = 2\n")
        hits = parse_hits(body, self.source)
        self.assertEqual([hit["rank"] for hit in hits], [1, 1])
        self.assertEqual([hit["group"]["index"] for hit in hits], [1, 2])
        self.assertEqual(hits[1]["group"]["query"], "second query")
        self.assertNotIn("-- query 2/2", hits[0]["text"])

    def test_preserves_explained_clipping_and_detects_changed_nonfirst_line(self):
        self.write_source("    node.name,\n    actual = 1\n")
        hit, = parse_hits("#1 example.py:1-2\n  1: e.name,\n  2: actual = 1\n", self.source)
        self.assertEqual(hit["source_display_differences"][0]["match_type"], "fragment_prefix_clipped")
        self.assertEqual(hit["source_display_differences"][0]["expected"], "    node.name,")
        self.assertEqual(hit["source_mismatches"][0]["line"], 2)
        hit, = parse_hits("#1 example.py:1-2\n  1: node.name,\n  2:     actual = 2\n", self.source)
        self.assertEqual(hit["source_display_differences"][0]["match_type"], "leading_indentation_removed")
        self.assertEqual(hit["source_mismatches"][0]["visible"], "    actual = 2")

    def test_short_truncation_is_classified_but_not_accepted_for_full_preview(self):
        line = "a" * 170
        self.write_source(line + "\n")
        body = "#1 example.py:1-1\n  1: " + line[:160] + "...\n"
        short, = parse_hits(body, self.source)
        full, = parse_hits(body, self.source, preview="full")
        self.assertEqual(short["source_display_differences"][0]["match_type"], "short_line_truncation")
        self.assertEqual(short["source_mismatches"], [])
        self.assertEqual(len(full["source_mismatches"]), 1)

    def test_unknown_numbered_headers_fail_instead_of_silently_reporting_zero(self):
        for body in ("#1 unsupported format\n", "-- query nonsense --\n#1 example.py:1-1\n"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse_hits(body, self.source)
        self.assertEqual(parse_hits("No matches.\n", self.source), [])


if __name__ == "__main__":
    unittest.main()
