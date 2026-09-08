# Read-only QA case selection

Selection date: 2026-09-08. Selected case: **`reflex-6`** (`reflex:6` in the existing task manifest). This is a single exploratory pilot, not a representative benchmark or a claim that zg will win.

## Baseline-only provenance

Screening uses the OpenCode × GLM-5.2 baseline trajectories from [CI run 34193646981](https://github.com/Cuiyus/zvec-grep/actions/runs/34193646981), run attempt 1, workflow commit `24ebe4f31059aeceb21976dd8bdedcc17dbe2eda`. The original pool is the five frozen `gate.auto_tasks` in `zg_bench/swe_qa/data/selection.json`, with three baseline trials per task. Trajectories identify OpenCode `1.18.4` and model `custom-openai/glm-5.2`.

The downloaded ZIPs contain both treatments; only members with a path component ending in `-baseline` were extracted and inspected. No pair report, zg trajectory, zg answer, or zg quality/cost outcome was used for selection. Artifacts are temporary analysis material in the ignored `runs/readonly-selection/` directory and are not committed. Gold and selection documents must remain outside the Agent workspace and zg index.

| Candidate | Pair artifact ID | Baseline input tokens, three trials | Baseline toolcalls, same order | Disposition |
| --- | --- | --- | --- | --- |
| `reflex-6` | `10043306615` | 131845 / 135054 / 129972 | 10 / 14 / 13 | Selected: repeated unknown-entry exploration; core answer supported by pinned source |
| `pylint-9` | `10043405667` | 104243 / 122500 / 59731 | 11 / 14 / 7 | Retain in candidate ledger; lowest-cost trial used webfetch, another answered a different nesting pattern; unsuitable as a clean quality-qualified cheap control without further review |
| `matplotlib-37` | `10043713458` | 86816 / 144923 / 141878 | 10 / 13 / 18 | Retain as explicit-symbol comparison/control candidate; question already supplies `FontInfo`, `postscript_name`, `FT2Font`; cost is largely reading and following known anchors |
| `streamlink-14` | `10043784809` | 127793 / 102886 / 251792 | 12 / 10 / 19 | Retain as retrieval/answer-quality challenge; answers choose different implementations or generic validation discussion, so not selected as consistently correct efficiency case |
| `xarray-32` | `10044400113` | 198768 / 303898 / 152333 | 21 / 24 / 16 | Retain as explicit-entry, cross-file reading candidate; more expensive than selected task but question supplies all three API names; performance claims also need careful source validation |

Input tokens are the historical ATIF `final_metrics.total_prompt_tokens` values, not newly measured provider billing. Cached tokens are reported below as a component and must not be added again. Toolcalls count each recorded `tool_calls` entry; shell subcommands are not separately expanded in this historical table. No old verifier reward is treated as answer correctness: the old shell verifier only provides a runtime/completion check.

## Why reflex-6

The unchanged question describes a "derived state variable" and "computation function accessor" without supplying the actual `ComputedVar` / `fget` symbols. All three baseline traces first try nearby words, then find the implementation and read it. This directly exposes the proposed opportunity of reducing entry-point discovery attempts. Selection does not use the largest token total; xarray is more expensive.

| Trial | Input | Cached input | Toolcalls | Recorded model requests | Greps before first source read | Empty grep results |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `reflex-6__TMvVJmP` | 131845 | 111104 | 10 | 9 | 4 | 1 |
| `reflex-6__iCYvQJ6` | 135054 | 111232 | 14 | 10 | 6 | 3 |
| `reflex-6__zvWXJ7j` | 129972 | 108928 | 13 | 9 | 5 | 4 |

- `TMvVJmP`: searches `computation` → `derived` → `computed` → `ComputedVar|cached_property|cached_var`, then reads `base.py`, `dep_tracking.py`, and `state.py`.
- `iCYvQJ6`: first searches JavaScript/TypeScript, then Python `computation`, `class.*Derived.*Var`, `[Dd]erived`, `accessor`, and `computed`. Reads large sections of `base.py` and parts of `dep_tracking.py`.
- `zvWXJ7j`: four empty searches for variants of `derived` / `computation_func`, followed by a broad class search and reads across the three implementation files.

Empty results are observable exploration, not automatically wasted work. Read volume includes useful implementation context. The opportunity is plausible, not a measured semantic-search benefit; an improved lexical query or native Agent behavior may eliminate it.

All three final answers correctly identify `ComputedVar.fget`, the getter's role as the input to static dependency analysis, and its value/caching role. Source review supports these central claims. There are limitations: `iCYvQJ6` cites `add_dependency` while describing automatic dependency registration (the automatic path is `BaseState._init_var_dependency_dicts`), and does not read `state.py`. It must not be described as a flawless or fully evidenced answer. No three-of-three full-evidence success or judge-quality score is claimed from this manual core-answer review.

## Frozen source and gold

The source is `https://github.com/reflex-dev/reflex.git` at `fe0f946dc0c240c6c1e513318c21db407e191c78`, matching the existing selection manifest, reference record, and dataset Dockerfile. The question is unchanged. The original upstream benchmark revision is `c13deac7a0d99b0ca2e593e004c4739475785b08`.

`reflex-6.json` records exact UTF-8 text from inclusive, one-based source ranges. `sha256` hashes the stored text including original line-ending newlines. The `sufficient_sets` structure is OR-of-AND: success requires covering every evidence unit in at least one listed set. The initial version has one audited implementation path, spanning accessor → getter analysis → dependency registration → dirty-state lookup → cache invalidation → getter execution. It does not require a particular tool or query order. Additional independently validated proof paths can be versioned without treating unannotated valid evidence as irrelevant.

The evidence units intentionally cover the mechanism and its connection to recomputation, not every optional claim an answer could make. Final answer quality must be checked separately: retrieving all spans does not establish that the answer used them correctly.

**Reference correction:** the inherited reference says `needs_update` detects dependency changes. At this pinned source, `base.py:2272–2286` checks the update interval and last-updated time; dependency changes instead use the reverse dependency map and `mark_dirty` to delete cached values. The source-grounded QA assessment should accept that correction and must not reward the inherited imprecision. Useful core facts are:

1. `fget` returns the user getter `_fget`; the getter computes the value when invoked.
2. With automatic dependencies enabled, `_deps` passes that getter to `DependencyTracker`; bytecode attribute accesses are collected into a state-to-variable dependency map. Executing the getter is not how this automatic scan discovers dependencies.
3. State setup builds reverse dependencies. Dirty source variables identify dependent computed variables, invalidate their cached values, and subsequent getter access recomputes them. Uncached getters run directly; interval-based refresh is a separate mechanism.

## Guardrails for the new experiment

Freeze this selection and gold before inspecting any new zg outcome. Run five new baseline trials and five new zg trials in independent sessions; do not reuse historical screening baseline costs as the comparison denominator. The formal requested zg version is `0.2.2`; the historical baseline does not contain zg, so the earlier treatment version does not affect these screening observations.

Use the unchanged read-only QA prompt and pinned source, with a reused compatible index and no source mutation or incremental indexing. Compare all planned runs, retain failures and missing usage explicitly, and report final-answer quality, sufficient visible evidence, input tokens, and toolcalls separately. Do not force a favorable outcome or reselect the case after seeing treatment results.

The five-task pool was already curated for retrieval-heavy QA; this selection cannot estimate the prevalence of such tasks. The explicit-symbol and cheaper candidate observations remain in the ledger above rather than being silently discarded. This pool does not establish a clean, universally cheap control. A broader benchmark should include a separately quality-verified low-exploration control and independent held-out tasks; ten runs of one case are not ten independent task samples.

Baseline trajectory SHA-256 values for reproducibility:

| Trial | SHA-256 |
| --- | --- |
| `reflex-6__TMvVJmP` | `dda3f5ddb60f395e50e17261d3b83b19a4775e1c619515366740cbbb43dd7cdf` |
| `reflex-6__iCYvQJ6` | `dfed56f377904caeca826ceffbab5b9f9146c0eb192b2c13d17efae6d7babeab` |
| `reflex-6__zvWXJ7j` | `8e3c543eda142afce93e0588bd20675f77dd13372c111c7b21bc9f5f3159a3e9` |
