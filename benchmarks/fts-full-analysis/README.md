# Full Retrieval-only comparison of native FTS analyzers

This follows the frozen-fragment experiment in run 44014198372. That experiment
measured lexical candidate coverage, not the changed analyzers' public Top10
after product ranking. Do not use it to choose a product default on final quality.

## Fixed action and protocol

Run SWE-QA20 and Quarry20 through PR417's public Rust MCP pipeline for each of
four preselected configurations: jieba/lowercase (baseline), jieba/lowercase/
English stemmer, standard/lowercase, standard/lowercase/English stemmer. Start
from `fc6abbab8e952f6bec2c2df812ecc131b48c62d8`; each experimental candidate changes
only the `FTS_CONFIG` constant in `rust/crates/zg-engine/src/domain/index.rs`.
The native stemmer's default language is English; no other parameter changes.

Use separate fresh indexes and package identities. Retain the existing local
embedding model, source locks, Gold, query strings, fragment budgets, metadata,
fusion, rule ranking, final limit=10, and full previews. Run hybrid, fts, vector
in the original order, five calls per question and mode: 600 calls per profile,
2,400 across four profiles. Do not rerank offline instead of executing the product.
Pure-vector results are a control and must be compared across all profiles.

Primary quality: question-averaged diagnostic Entity Recall@10. Also report
Entity MRR@10, nDCG@10, Hit/Recall@1/5/10, original File metrics, per-case gains
and losses, output size, latency and stable ordering. SWE-QA uses the unchanged
17 eligible questions / 88 Entity Gold for diagnostic aggregates; retain the
three disputed questions and all 20 raw cases separately. Quarry keeps 20
questions / 107 published partial-positive targets, with repository/language
breakdowns. No failures or unknown scores are turned into zeros or dropped.

Capture Gold index coverage and FTS/vector fragment, fused, loaded, static-rule,
and selected ranks with PR417's unchanged `--gold-stages` path. Derive FTS
coverage curves at 10, 20, 50, 100, 200 from the original 200-fragment list;
these are diagnostic prefixes, not reruns with smaller candidate budgets.
This distinguishes a saturated Recall@200 from rank movement inside that
window, then checks which movements affect the actual product Top10. Do not
claim that changing the production candidate budget was evaluated.

All runs use the same pinned harness. The normal protocol tests run against
each candidate's unmodified upstream benchmark/workflow files. The experiment
workflow intentionally retains only the two requested suites. No answering
agent, query rewriting, English-only Gold changes, ranking edits, code-tokenizer
implementation, or global default rollout is included.

## Read-only physical audit

After public retrieval finishes, `run.py` opens each physical fragment collection
read-only using the native library shipped in the evaluated package. It checks
the reported analyzer, exports exact fragment text/IDs/Entity mappings, hashes
stored FP16 vector bytes without float conversion, and captures native FTS top200.
Hashes use fragment-ID order so iterator/build order does not affect identity.
Native-to-public stage ranks are checked during analysis. The before/after hashes
of all original collection files must match. The small native test verifies that
the audit reads actual persisted FP16 bytes rather than transient input vectors.

Compare source/model manifests, fragment/text hashes, vector hashes and pure-vector
public rankings across configurations. A discrepancy must be investigated and
reported; it cannot be silently attributed to FTS. The audit does not mutate
indexes, recompute embeddings, or supply the final quality scores.

## Run and retrieve

Dispatch `.github/workflows/retrieval-only.yml` at the frozen experiment harness
with `candidate_ref=<profile SHA>`, `analyzer=<profile name>`, `embedding=local`,
and `gold_stages=true`. Candidate manifests and exact one-file diffs are saved
under the ignored `output/fts-full-retrieval-20261117` directory. Record the
harness SHA, run IDs, package identities and all failed attempts there too.

Each run uploads the usual `retrieval-zg-report/evidence` and
`retrieval-quarry-report/evidence`, plus `sweqa-physical-audit` and
`quarry-physical-audit`. Raw artifacts stay outside tracked source. Preserve
the earlier candidate-only report and publish the full-pipeline findings as
a separate report, stating whether they revise the earlier recommendation.
