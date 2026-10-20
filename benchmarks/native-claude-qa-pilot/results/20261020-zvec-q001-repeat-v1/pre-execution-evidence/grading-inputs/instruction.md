Investigate the following behavior using only the supplied frozen source tree. This is a read-only code question: do not modify the implementation or search the web. Ground your answer in the implementation, with repository-relative file paths and line ranges for the important causal steps.

A service inserts documents into a zvec collection and exits abruptly before an explicit flush. In a new process, opening the collection recovers the documents, and they can be counted and fetched. That process performs `optimize()` without inserting any additional documents. The call succeeds. Statistics from the same open handle still look healthy, but opening the collection again in another process reveals that the recovered documents are missing.

A control collection populated and optimized in the original process, without a crash in between, does not show this loss. Assume the collection is small enough that automatic buffer or segment rollover does not happen while inserting or replaying these documents.

Explain:

1. Which internal state differs between the recovery path and the ordinary insertion path, and why the recovered data can initially be read.
2. The call sequence and exact condition that allow optimization to succeed without preserving that data for the next reopen.
3. Why a successful return and same-handle document/index statistics do not establish durability here.
4. The smallest repair principle that restores the intended lifecycle, and a regression-test sequence that would detect this failure. Describe the repair and test; do not implement them.

Distinguish what you can establish from the source from anything that would require executing a test. Do not claim to have run a reproduction unless you actually did so.
