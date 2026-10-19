# 原生 Claude Code + Qwen：q001 单次开发对照

本次满足运行前冻结的 **reported-input 开发候选条件**：baseline 与 zg 均为 8/10、通过质量门槛，zg 含缓存的输入 token 减少 63.27%。原生 agent 执行耗时减少 20.24%；计入安装、首次建索引和清理后，完整 slot 耗时减少 5.24%。这是一对开发结果，尚未证明收益稳定，也没有建立一组稳定有利的 Benchmark。

| 指标 | Baseline | zg | zg 相对变化 |
| --- | ---: | ---: | ---: |
| 原始 rubric 得分 / 质量门槛 | 8/10，PASS | 8/10，PASS | 持平 |
| 输入 token，含缓存读写 | 2,369,175 | 870,234 | −63.27% |
| 输出 token | 32,639 | 25,303 | −22.48% |
| 持久化 LLM 调用 | 29 | 18 | −37.93% |
| 工具调用 | 42 | 31 | −26.19% |
| 原生 agent 执行耗时 | 1,055.896 s | 842.205 s | −20.24% |
| 完整 slot 耗时 | 1,107.287 s | 1,049.303 s | −5.24% |

zg 安装、授权、建索引及 MCP 配置共 171.573 秒，其中索引 162.648 秒。它们已计入 zg 的完整 slot；表中的原生执行耗时没有包含这些准备阶段。输入 token 是 CLI `modelUsage` 的未缓存输入、缓存读取和缓存创建之和，和完整持久化会话逐项相等；不是账单费用，也不包含 embedding 用量。原始 Harbor 阶段、审计、导出和 collection 时间另见 [summary.json](summary.json) 与 [collection](raw/live/collection/collection.json)。

## 执行与评分

任务沿用 zvec closed issue #594 的原始 q001：崩溃恢复后没有新增写入，`optimize()` 成功且同一 handle 的统计正常，但下一次打开丢失数据。问题、隐藏 rubric 和修复前源码均未修改。

- [Fork CI run 39338284225](https://github.com/Cuiyus/zvec-grep/actions/runs/39338284225)，attempt 1，执行 head `d40d7fa4ab5139fc9e728d7d5b035efe226aa5b4`。按 baseline → zg 顺序各执行一次，无替换或整次重试。
- Claude Code 2.1.212、Harbor 0.18.0、Qwen3.8-max；复用主干 benchmark 的原生 agent 路径与远端已有配置。
- Rust zg 固定于当时上游 main `a424e0ce06868dc9ad8f142f57de59b8f9b58699`；任务源码固定于 `alibaba/zvec` 的 `ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d`。
- [独立执行/计量审查](review/collection-review.json) 69/69 通过；[来源审查](review/source-audit.json) 覆盖全部 73 次工具调用。两组均只有一个 root 会话，没有实际子 agent；1232 个源码文件前后匹配，未见联网搜索、实现修改或执行仓库程序/测试。
- zg 的 1 次 MCP 调用成功，使用正确 `/workspace`，返回与 WAL 恢复有关的真实源码。检索相关性与最终答案质量分别审查。
- [匿名评分](grading/grades.json)只接收原始答案、题目、rubric 和冻结源码；不接收条件、轨迹、资源指标或历史评分。这是独立模型源码审阅，不是官方 SWE-QA 或人工评分。随机映射在答案产生前生成，评分后才用于汇总。

两组逐项得分均为 `C1=2, C2=2, C3=2, C4=0, C5=1, C6=0, C7=1`，核心事实及引用通过，无关键矛盾。共同缺项是：没有完整解释索引统计如何来自 metadata（C4），也没有说明新恢复路径中打开成员 WAL 失败时不能继续返回成功（C6）。原报告另外保留了非关键的过度概括和辅助事实问题，PASS 不表示答案毫无错误。

运行前约定的 C6 lifecycle-only 敏感性分析均为 9/10、PASS，不替代主分；候选判定不变。因为 baseline 也通过，本次不满足“baseline 失败而 zg 通过”的独立质量候选定义。

[评分一致性复核](review/grade-consistency.json)支持两组主分与门槛判定，同时指出原评分记录有三处将审阅者补充核验的源码区间误标为答案实际引用。实际核心引用和答案本身的测试步骤仍足以支撑对应得分，因此不改分；原评分文件与具体更正说明一并保留，不能把这些补充区间算作 solver 的引用。

## 证据与边界

[冻结计划](../../live-plan-v2.json)、[执行决定](../../live-decision-v2.json)与[评分契约](../../grader-contract-v1.json)均早于本次答案。原始 collection 的“待独立审查”状态保持原样，完成后的合并判定写在 [summary.json](summary.json)。

[retention.json](retention.json)核对了 152 个原始文件副本，其中 123 个为 artifact raw 文件。完整原始 ZIP SHA-256 为 `75f0c2ebdb295a982c4a5e4f9765d83c3434ea70f086a15045b8cc563ebf928f`，artifact ID `12229092834`。完整 ZIP 和其中重复的 123 MB 安装包保存在 fork artifact 与原始本地归档，未再次复制进 Git；安装包 hash、原路径和省略原因明确记录。原始答案、原生 stdout、持久化会话、ATIF、源码文件清单、执行元数据和日志均保留。后续审查与评分保存在独立目录。

该任务已在之前阶段被观察过，且本次是固定顺序的单次对照。不能据此作泛化、稳定收益、独立计费或严格因果归因。此前 custom Claude 和 OpenCode 的确认失败仍保留，计量口径不同，不与本次合并。仓库较新也不能证明没有进入模型训练。

本阶段已按冻结规则停止。下一步应在单独冻结的确认计划中复用同一任务、模型、原生 agent、源码与评分要求，保留每次结果；确认可重复后再扩展新 issue，不能把这次好结果直接当作稳定 Benchmark 集合。
