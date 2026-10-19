# Issue #583 candidate evidence (draft)

Retain as an eligible future QA candidate. This scratch artifact is not part of the current q001 confirmation batch; no solver or reproduction was run.

- [Neutral question](solver/instruction.md)
- [Eligibility and limitations](eligibility.json)
- [Draft 10-point rubric](evaluator/rubric-draft.json)
- [Exact code citations and excerpt/file hashes](evaluator/evidence.json)
- [Exported pre-fix source inventory](source-manifest.json)
- [Raw primary provenance retrieval manifest](provenance/retrieval-manifest.json)
- [Actual merged patch](provenance/fix-merge.patch)
- [Earlier reserve-candidate record](provenance/prior-candidate-record.json)

The frozen source is the actual single parent `e906168cc19378358d8fa1c1559f48e4b920aa37` of fix `a90ec5b1d658d55cdb5f4f0f158fa21d82cfbc59` ([issue](https://github.com/alibaba/zvec/issues/583), [PR](https://github.com/alibaba/zvec/pull/584)). The public issue's explanation has inaccuracies; the rubric follows source, especially consumption/reset of the inverted filter and loss of explicit-empty candidate semantics. A null bitmap and globally absent remaining filter are not necessary conditions.

For a future run, expose only `source/` and `solver/instruction.md`; all other content is evaluator/provenance material. Keep Git history and issue/PR web access outside the solver environment. The authentic pre-fix code/tests remain intact. The reported scenario was reproduced by the reporter on 0.5.1; this exact snapshot has only been source-inspected here. Repository novelty is not a training-contamination guarantee.

`SHA256SUMS` covers every other file in this directory, including every exported source byte. `source-manifest.json` additionally records the export archive and canonical inventory hashes. Source snippets in `evaluator/evidence.json` retain original line endings for hashing. The raw GitHub responses were preserved unchanged.
