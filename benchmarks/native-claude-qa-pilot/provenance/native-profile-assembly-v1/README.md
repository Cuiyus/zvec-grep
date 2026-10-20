# Independent-profile collection replay

The unchanged native collector checks two profiles together, including their common-tool parity. It cannot collect a single profile in isolation: the original body compares both tool lists. A future parallel allocation must retain each original worker record and assemble both before joint collection.

The portable [replay](replay.py) splits one already completed q001-r03 record into two fixture records, joins their original slots and profile-specific log trees, then calls the unchanged native collector. Eighteen answer/usage/timing/tool/validity comparisons match the original collection. All 111 original live files are rehashed before and after and remain unchanged; unexpected files and symlinks are rejected.

[verification.json](verification.json) binds the exact old artifact receipt and collector source. The logged command uses the q001-r03 files already retained in this repository. Its old inner runtime-plan identity is preserved; no old observation is relabeled as q005 or pooled into a new cohort.

This is parser/assembly fidelity evidence using a single historical execution host. It performs no Docker or solver invocation and proves no fresh parallel execution, hardware equivalence, source-scope validity, QA effect or admission to a new paid run. New allocation controls and fork preflight remain required.
