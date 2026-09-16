from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import office_markdown as md
import runner
import judge


class OfficeMarkdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_pptx(self):
        path = self.root / "report.pptx"
        a, c, r = md.NS['a'], md.NS['c'], md.NS['r']
        with zipfile.ZipFile(path, "w") as z:
            z.writestr('ppt/presentation.xml', f'<p:presentation xmlns:p="urn:p" xmlns:r="{r}"><p:sldIdLst><p:sldId r:id="second"/><p:sldId r:id="first"/></p:sldIdLst></p:presentation>')
            z.writestr('ppt/_rels/presentation.xml.rels', '<Relationships><Relationship Id="first" Type="slide" Target="slides/slide1.xml"/><Relationship Id="second" Type="slide" Target="slides/slide2.xml"/></Relationships>')
            for n in [1, 2]:
                z.writestr(f'ppt/slides/slide{n}.xml', f'<slide xmlns:a="{a}"><a:p><a:r><a:t>正文{n}</a:t></a:r></a:p></slide>')
            z.writestr('ppt/slides/_rels/slide2.xml.rels', '<Relationships><Relationship Id="chart" Type="chart" Target="../charts/chart1.xml"/></Relationships>')
            z.writestr('ppt/charts/chart1.xml', f'<c:chartSpace xmlns:c="{c}"><c:ser><c:tx><c:strRef><c:strCache><c:pt idx="0"><c:v>入职</c:v></c:pt></c:strCache></c:strRef></c:tx><c:cat><c:strLit><c:pt idx="1"><c:v>行政部</c:v></c:pt><c:pt idx="3"><c:v>财务部</c:v></c:pt></c:strLit></c:cat><c:val><c:numLit><c:pt idx="3"><c:v>11</c:v></c:pt><c:pt idx="1"><c:v>12</c:v></c:pt></c:numLit></c:val></c:ser></c:chartSpace>')
        return path

    def test_presentation_order_and_chart_category_value_alignment(self):
        path = self.make_pptx()
        before = path.read_bytes()
        text, audit = md.convert_file(path, path.name)
        self.assertLess(text.index('正文2'), text.index('正文1'))
        self.assertIn('| 1 | 行政部 | 12 |', text)
        self.assertIn('| 3 | 财务部 | 11 |', text)
        self.assertEqual(audit['counts']['slides'], 2)
        self.assertEqual(audit['counts']['charts'], 1)
        self.assertEqual(audit['missing_atoms'], 0)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual((text, audit), md.convert_file(path, path.name))

    def test_word_table_keeps_runs_cells_and_merged_cell_metadata(self):
        path = self.root / 'table.docx'
        w = md.NS['w']
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('word/document.xml', f'<w:document xmlns:w="{w}"><w:body><w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>前台</w:t></w:r><w:r><w:t>文员</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>172</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        text, audit = md.convert_file(path, path.name)
        self.assertIn('| 前台文员 [colspan=2] | 172 |', text)
        self.assertEqual(audit['counts']['table_rows'], 1)

    def test_conversion_is_task_blind_and_reports_empty_legacy_corrupt_inputs(self):
        source = self.root / 'workspace'; source.mkdir()
        p = self.make_pptx()
        (source / 'unrelated.pptx').write_bytes(p.read_bytes())
        (source / 'original.txt').write_text('existing TXT')
        (source / 'empty.docx').touch()
        (source / 'old.doc').write_bytes(b'legacy')
        (source / 'broken.xlsx').write_bytes(b'not a zip')
        result = md.convert_workspace(source, self.root / 'manifest.json')
        self.assertEqual(result['status_counts'], {'conversion_failed':1, 'empty_original':1, 'legacy_unsupported':1, 'converted':1})
        self.assertTrue((source / 'unrelated.pptx.md').is_file())
        self.assertEqual((source / 'original.txt').read_text(), 'existing TXT')
        with self.assertRaises(FileExistsError):
            md.convert_workspace(source, self.root / 'manifest2.json')

    def test_worksheet_keeps_addresses_cached_values_formulas_formats_and_merges(self):
        path = self.root / 'cells.xlsx'
        s, r = md.NS['s'], md.NS['r']
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('xl/workbook.xml', f'<workbook xmlns="{s}" xmlns:r="{r}"><sheets><sheet name="部门" sheetId="1" r:id="s1" state="hidden"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="s1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/sharedStrings.xml', f'<sst xmlns="{s}"><si><t>行政部</t></si></sst>')
            z.writestr('xl/styles.xml', f'<styleSheet xmlns="{s}"><numFmts><numFmt numFmtId="164" formatCode="0.00%"/></numFmts><cellXfs><xf numFmtId="164"/></cellXfs></styleSheet>')
            z.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{s}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><f>1/10</f><v>0.1</v></c></row></sheetData><mergeCells><mergeCell ref="A1:A2"/></mergeCells></worksheet>')
        text, audit = md.convert_file(path, path.name)
        self.assertIn('State: hidden', text)
        self.assertIn('Merged cells: A1:A2', text)
        self.assertIn('| A1 | 行政部 |  | 0.00% |', text)
        self.assertIn('| B1 | 0.1 | 1/10 | 0.00% |', text)
        self.assertEqual(audit['counts']['worksheet_cells'], 2)

    def test_large_utf8_sidecars_are_split_without_truncating_any_content(self):
        text = ('中国|é\n' * 300000) + ('单' * 600000)
        parts = md.chunks(text)
        self.assertGreater(len(parts), 1)
        self.assertEqual(''.join(parts), text)
        self.assertTrue(all(len(p.encode()) <= md.PART_BYTES for p in parts))

    def test_both_arm_prompts_share_mapping_notice_and_judge_can_see_chart_data(self):
        path = self.make_pptx()
        with patch.dict('os.environ', {'WORKSPACE_QA_CORPUS_VARIANT':md.VARIANT}):
            for zg in [False, True]:
                self.assertIn(md.COMMON_NOTICE, runner.instruction('原始任务', 'answer.csv', zg=zg))
            self.assertIn('| 1 | 行政部 | 12 |', judge.source_text(path, path.read_bytes()))
        with patch.dict('os.environ', {'WORKSPACE_QA_CORPUS_VARIANT':'original'}):
            self.assertNotIn(md.COMMON_NOTICE, runner.instruction('原始任务', 'answer.csv', zg=False))
            self.assertNotIn('行政部', judge.source_text(path, path.read_bytes()))


if __name__ == '__main__':
    unittest.main()
