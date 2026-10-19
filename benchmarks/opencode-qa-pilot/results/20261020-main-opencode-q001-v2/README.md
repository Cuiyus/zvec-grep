# Original q001: repaired native OpenCode development pair

The one scheduled [fork run](https://github.com/Cuiyus/zvec-grep/actions/runs/39315776778) completed with one valid pair. Both blinded answers scored **9/10** and passed the unchanged original gate. zg used **48.29% less persisted-session input** and one successful relevant `/workspace` MCP retrieval. This meets the frozen **scoped input candidate** rule; it does not establish stable benefit or complete the favorable-set goal.

| Measure | Baseline | zg |
|---|---:|---:|
| Source-grounded answer quality | 9/10, pass | 9/10, pass |
| Persisted-session input, including cache | 2,824,477 | 1,460,577 (-48.29%) |
| Output, including reasoning | 25,602 | 18,657 (-27.13%) |
| Tool calls | 67 | 59 |
| Solver wall, measured collection overhead removed | 820.97s | 581.58s |
| Native agent setup | 17.40s | 173.97s |
| Outer slot wall | 880.20s | 777.46s |

The baseline loses C7 (fresh-process final validation and a proper control); zg loses C4 (index completeness derived from metadata). Both satisfy C1–C3 and core citation checks. The [blind review](blinded-review/review.json) retains other unsupported claims, including incomplete optional repair suggestions.

All 67 baseline and 59 treatment tool calls were reviewed. Each native tree has one root and no child sessions. The [MCP review](native-audit/mcp-relevance-review.json) verifies relevant recovery/WAL previews and subsequent focused source reads; it does not prove a counterfactual causal contribution.

Treatment setup includes 150.637s of fresh remote indexing. Outer wall fell 11.67%, but both arms shared the Docker image/layer cache; this is not an independently cleared-image timing comparison. Dollar costs and whole-provider token totals are unknown.

This phase is closed. Only a separately frozen confirmation may follow. The earlier invalid OpenCode pair and all 22 Claude rows remain separate.

[Structured summary](summary.json) · [execution audit](execution-review/execution-review.json) · [native/source audit](native-audit/final/audit.json) · [original artifact ZIP](artifact.zip). Run `python3 report-results.py` here to rebuild this summary from retained evidence. The separate audit reproducers have additional pinned runtime/source dependencies documented in their reports.
