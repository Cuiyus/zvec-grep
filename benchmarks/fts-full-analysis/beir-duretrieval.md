# PR417 FTS comparison: BEIR and DuRetrieval extension

This is the user-requested continuation of the four-profile SWE-QA/Quarry
experiment documented in [README.md](README.md). Run the same four immutable
product candidates through the complete public MCP Retrieval-only pipeline on
the two remaining frozen suites. Do not rerun or overwrite the earlier results.

## Fixed inputs and execution

| Profile | Tokenizer | Ordered filters | Product commit |
| --- | --- | --- | --- |
| jieba_lower | jieba | lowercase | fc6abbab8e952f6bec2c2df812ecc131b48c62d8 |
| jieba_stem | jieba | lowercase, stemmer | b2e91dbe7e6e9f5d6115360d56a7ed0b6558204d |
| standard_lower | standard | lowercase | 29fd3d612d2c44bafe059c7e9d2cec73c883d49e |
| standard_stem | standard | lowercase, stemmer | fb5576a71f34d9d755ae9b56db479057417d996c |

Each nonbaseline product changes only `FTS_CONFIG`. Stemmer keeps the native
English default, including for Chinese inputs; do not silently substitute a
different language, dictionary or analyzer. Keep source locks, queries, Gold,
fragmentation, original candidate budgets, fusion, rule ranking and limit=10.

Use the unmodified pilot registry and runner from PR417:

- BEIR: 20 queries (10 SciFact, 4 NFCorpus, 3 ArguAna, 3 FiQA), full pinned
  corpora. Six separately built indexes include ArguAna's per-query self-match
  exclusions. Do not reduce corpus size or select a different sample.
- DuRetrieval: the existing 10 original Chinese dev queries and all 100,001
  passages in the pinned C-MTEB corpus subset. This is not a full DuReader score.
- Both use the protocol's `local/potion-multilingual-128m`, CPU, fresh indexes,
  full preview, original hybrid/fts/vector order, five calls per question/mode.
  These are 8 suite/profile groups and 1,800 public MCP calls in total.

The experiment workflow runs only BEIR and DuRetrieval. The unchanged upstream
protocol tests run from the selected product candidate; the two-suite harness
must not be confused with the upstream four-suite production workflow. Pin the
new harness commit once, dispatch four runs with `embedding=local`, matching
`analyzer`, exact `candidate_ref` and `gold_stages=true`.

## Scoring and comparisons

Use the same official scoring version and original native output ranks. Report
FTS candidate Recall@200 and final FTS/Hybrid Recall@10, MRR@10 and nDCG@10,
with both original document/File qrels and diagnostic Entity Gold. Retain all
Hit/Recall@1/5/10, Vector controls, five-call stability, per-case gains/losses,
BEIR per-dataset breakdown, latency and output volume as supporting evidence.

Entity denominators remain the frozen manifests: BEIR has 22 established
targets over 14 questions; six unresolved SciFact questions remain in File
scoring and raw results but are unscored for Entity. DuRetrieval has 48
established targets over all ten questions. Preserve BEIR evidence alternatives
and all required spans; do not replace group completion with any-span matching.
Five calls are averaged within each question, then questions are equally
weighted. Unknowns, failures and unscored cases must not become zeros.

Read stage traces from the actual public calls. Record original route attempts;
if adaptive retrieval exceeds 200, distinguish its actual coverage from the
diagnostic prefix. Prefix curves do not constitute a smaller-budget pipeline
experiment. No additional ranking, tokenizer, model or Gold ablation is included.

## Input and evidence checks

Reuse the read-only native physical audit against the library shipped in the
evaluated package. The Python driver now accepts the two text suites and checks
their expected index/query counts; native collection inspection is unchanged.
Verify source index files remain unchanged and compare exact fragment text/IDs,
Entity ownership and persisted FP16 vector bytes across profiles. Cross-check
native FTS ranks with captured watched Entities. Retain model/index settings,
package identity, corpus source locks and all pure-vector output differences.
The earlier experiment found vector ranking drift across independent builds;
do not promise exact Vector equality or attribute Hybrid drift entirely to FTS.

Store manifests, commands, downloaded artifacts, checksums, analysis and any
failed attempts under ignored `output/fts-beir-du-retrieval-20261117/`. Each run
uploads `retrieval-{beir,duretrieval}-{report,evidence}` and
`{beir,duretrieval}-physical-audit`. Preserve the earlier eight-group results;
publish a separate report and a clearly labeled combined comparison if useful.
