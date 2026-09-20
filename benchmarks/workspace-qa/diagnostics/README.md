# Qoder 1.1.45 instruction/routing diagnostic

This is not a Workspace-Bench trial, an efficacy comparison, or a new Task 192 score.
The completed Task 192 pair and its zero QA zg calls are retained unchanged.

The published Qoder npm bundle is pinned by SHA-256. A separate diagnostic Docker
layer adds two passive observations: the loaded memory context and the native
transport-request normalization entry. Installation still runs the released
`zg install --target qoder --yes`; its settings, MCP configuration and AGENTS.md
remain subject to the existing standard-install integrity checks.

The observer records whether the complete installed guidance (excluding HTML
comments, which Qoder strips) is present in a model request's system/user content,
which role carries it, whether its mixed/cross-document routing lines are present,
and whether the zg tool schema is present. It never records the prompt body,
credential values, assistant answer or hidden reasoning. Native session artifacts
remain separate, as in the existing harness. This verifies client request assembly,
not independent receipt or processing inside Qoder's hosted model service.

Two fresh sessions use the same three tiny, synthetic business documents, native
Read/Grep/Glob + zg search, Qwen3.8-Max, zg 0.2.2 and remote Qwen embedding:

1. **natural**: a cross-document financial comparison/causal analysis question.
2. **routing-reminder**: the same question plus an explicit reminder to follow the
   already-loaded user-level retrieval routing guidance. It does not name the tool
   or supply a query; it is a different diagnostic prompt, not a fair efficacy arm.

Each session is limited to six model requests, twelve tool calls, 180k cumulative
input tokens and 180 seconds, with one native retry. The CLI requests 2,048 tokens
per model output; this is not a verified effective cap. The first diagnostic run
observed `parameters.max_tokens = 32000` in all six client request objects despite
the `--max-output-tokens 2048` argument. See the
[2026-09-20 results and limitations](../reports/qoder-zg-routing-diagnostic-2026-09-20.md).
The index is built once; each session has a writable copy and fresh HOME/install.
Zero use is preserved. Missing observation, observer errors or failed installation
fails the diagnostic gate; budget exhaustion can still provide a valid observation
of early tool routing, and is explicitly retained in the result.

No real workspace archive, PDF conversion or judge call is needed. The original
Task 192 corpus manifest was also checked: it contains no project AGENTS.md or
`.qoder` rules that could override the user-level installation guidance.

Run through `workspace-qa-routing-diagnostic.yml`, explicitly dispatched or opted
in with `[zg-routing-diagnostic]` in the HEAD commit message. Neither the normal
benchmark image nor its default run settings are changed.

Local verification: four observer tests (actual memory vs tool-description-only,
partial/missing memory, output redaction), Python parsing, exact-bundle patch
anchors/SHA validation, Node syntax check of the patched bundle, and workflow YAML
parsing. CI repeats the observer tests and exact-bundle patch checks.
