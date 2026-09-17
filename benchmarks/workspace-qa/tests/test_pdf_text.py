import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import judge
import pdf_audit
import pdf_judge_evidence as pe
import pdf_text as pdf
import runner

HAVE_PDF = all(importlib.util.find_spec(n) for n in ("pypdf", "pypdfium2"))


@unittest.skipUnless(HAVE_PDF, "Install the pinned PDF pilot requirements")
class PdfPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_pdf(self, path):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        for text in ("First page revenue 123.45", "Second page risk demand 67.89"):
            page = writer.add_blank_page(width=612, height=792)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
                DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
            content = DecodedStreamObject()
            content.set_data(f"BT /F1 12 Tf 40 740 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(content)
        path.parent.mkdir(parents=True, exist_ok=True)
        writer.write(path)
        return path

    def test_uniform_sidecars_keep_page_order_source_bytes_and_existing_files(self):
        source = self.root / "workspace"; source.mkdir()
        original = self.make_pdf(source / "unrelated.pdf")
        before = original.read_bytes()
        (source / "empty.pdf").touch()
        (source / "fake.pdf").write_text("not an actual PDF")
        (source / "existing.txt").write_text("unchanged")
        manifest = pdf.convert_workspace(source, self.root / "manifest.json")
        body = (source / "unrelated.pdf.txt").read_text()
        self.assertLess(body.index("First page"), body.index("Second page"))
        self.assertIn("PDF page 2 / 2", body)
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(manifest["status_counts"], {"empty_original": 1, "not_pdf_original": 1, "converted": 1})
        self.assertEqual((source / "existing.txt").read_text(), "unchanged")
        with self.assertRaises(FileExistsError):
            pdf.convert_workspace(source, self.root / "collision.json")

    def test_independent_coverage_gate_rejects_sidecar_corruption(self):
        source = self.root / "workspace"; source.mkdir()
        original = self.make_pdf(source / "report.pdf")
        review = {"files": [{"filename": original.name, "source_sha256": pdf.sha(original.read_bytes()),
                              "page_count": 2, "reviewed_samples": [{"page": 1, "anchors": ["123.45"]}]}]}
        manifest = pdf.convert_workspace(source, self.root / "manifest.json")
        self.assertEqual(pdf_audit.verify(source, manifest, self.root / "audit", review)["status"], "passed")
        (source / "report.pdf.txt").write_text("corrupted")
        with self.assertRaises(ValueError):
            pdf_audit.verify(source, manifest, self.root / "audit-bad", review)

    def test_pre_qa_selection_preserves_original_pages_and_detects_rewritten_evidence(self):
        task = self.root / "task"; task.mkdir()
        path = self.make_pdf(task / "data/report.pdf")
        metadata = {"task": "Read report and discuss risk", "rubrics": ["Output format", "Discuss demand risk"],
                    "rubric_types": ["Basic", "Outcome"],
                    "data_manifest": [{"filename": "report.pdf", "stored_relpath": "data/report.pdf"}]}
        meta = task / "metadata.json"; meta.write_text(json.dumps(metadata))
        _, model = judge.settings()
        def completion(**kwargs):
            payload = json.loads(kwargs["messages"][1]["content"])
            self.assertIn("First page", payload["complete_pdf_text"])
            self.assertIn("Second page", payload["complete_pdf_text"])
            self.assertNotIn("candidate_answer", payload)
            return {"model": model, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
                {"company_pages": {key: [2] for key in pe.COMPANY_SECTIONS},
                 "criteria": [{"id": 0, "pages": []}, {"id": 1, "pages": [2]}]})}}]}
        with patch.dict(os.environ, {"GLM_API_KEY": "test-only-key"}):
            packet = pe.prepare(meta, task, self.root / "evidence", completion_fn=completion)
        evidence = pe.load_verified(packet, meta, task, judge.MAX_SOURCE_BYTES)
        self.assertIn("Second page", evidence["sources"][0]["text"])
        self.assertNotIn("First page", evidence["sources"][0]["text"])
        prompt = judge.build_messages(evidence, "answer")
        self.assertIn("not full documents", prompt[0]["content"])
        self.assertEqual(evidence["sources"][0]["sha256"], pdf.sha(path.read_bytes()))
        evidence["sources"][0]["text"] = "fake evidence"
        packet.write_text(json.dumps(evidence))
        with self.assertRaises(judge.JudgeError):
            pe.load_verified(packet, meta, task, judge.MAX_SOURCE_BYTES)

    def test_page_selector_rejects_nonexistent_pages_omitted_rubrics_and_model_changes(self):
        def response(criteria, model="glm-test", company=None):
            return {"model": model, "choices": [{"finish_reason": "stop",
                "message": {"content": json.dumps({"criteria": criteria,
                    "company_pages": company if company is not None else {key: [1] for key in pe.COMPANY_SECTIONS}})}}]}
        for rows in ([{"id": 0, "pages": [3]}], [{"id": 0, "pages": [True]}], []):
            with self.assertRaises(judge.InvalidAssessmentError):
                pe.parse_selection(response(rows), "glm-test", 1, 2)
        for company in ({}, {key: [] for key in pe.COMPANY_SECTIONS},
                        {key: [3] for key in pe.COMPANY_SECTIONS},
                        {key: [True] for key in pe.COMPANY_SECTIONS}):
            with self.assertRaises(judge.InvalidAssessmentError):
                pe.parse_selection(response([{"id": 0, "pages": []}], company=company), "glm-test", 1, 2)
        with self.assertRaises(judge.JudgeError):
            pe.parse_selection(response([{"id": 0, "pages": [1]}], "different"), "glm-test", 1, 2)

    def test_mapping_notice_is_shared_without_forcing_retrieval_and_packet_is_required(self):
        with patch.dict(os.environ, {"WORKSPACE_QA_CORPUS_VARIANT": pdf.VARIANT,
                                   "WORKSPACE_QA_PDF_EVIDENCE": "", "WORKSPACE_QA_PDF_EVIDENCE_SHA256": ""}):
            for zg in (False, True):
                self.assertIn(pdf.COMMON_NOTICE, runner.instruction("original task", "report.md", zg=zg))
            with self.assertRaises(judge.JudgeError):
                judge.load_evidence(self.root / "metadata.json", self.root)


if __name__ == "__main__":
    unittest.main()
