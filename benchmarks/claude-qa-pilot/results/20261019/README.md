# Claude Code + Qwen3.8-max QA 开发验证

本次先验证两条路线各一个 case，再对 q001 做两对确认，并冻结扩展两个新 issue case；随后在原 q001 上单独测试一对远端 Qwen embedding。使用 `Claude Code 2.1.212 + qwen3.8-max`。zg 是各次冻结时上游主干 `a424e0ce06868dc9ad8f142f57de59b8f9b58699` 的 Rust 构建。前序阶段配本地 `potion-code-16m-v2`，远端阶段配 `qwen/qwen3.7-text-embedding`，分别报告。全部新增 CI 在 `Cuiyus/zvec-grep` 执行。

**结论：此前单题开发 pair 的效率信号没有在两对确认试验中复现。** 开发 pair 的 zg input 曾少 51.3%、wall 少 18.5%，两组均为 9/10 且通过；随后冻结的两对确认试验中，zg input 分别增加 108.3% 和 67.6%。第 2 对两组均为 8/10 且通过；第 3 对 baseline 为 9/10，但核心引用不完整而未通过，zg 为 8/10 且通过。四次均完成，不能宣称稳定效率优势。新增 q002 两组 10/10 且通过，但 zg input 增加 82.8%；q003 从 baseline 6/10 提升到 zg 9/10，两组仍未达到核心源码证据门槛，zg input 增加 36.3%。两题整组信号规则未通过。SWE-QA 有效检索迭代仍是质量 9→10 分、input 增加 118% 的取舍。远端 Qwen 单题对照中，zg 8/10 且通过核心引用门槛，baseline 9/10 但未通过；zg input 和 wall 仍增加 25.7% / 48.6%。这反映引用、事实覆盖与资源之间的取舍。所有开发失败与不利结果保留。

## 实验设计

| 路线 | 冻结 case | 源码版本 | 用途 |
| --- | --- | --- | --- |
| SWE-QA | reflex-6：computed var 的 getter、依赖分析和失效重算 | `reflex-dev/reflex@fe0f946dc0c240c6c1e513318c21db407e191c78` | 验证切换 Claude Code + Qwen 后的行为 |
| Closed-issue QA | zvec-q001：崩溃恢复后 optimize 的持久性问题 | `alibaba/zvec@ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d` | 验证跨文件因果解释的新题型 |

zvec-q001 来源于已关闭的 [issue #594](https://github.com/alibaba/zvec/issues/594) 和 [修复 PR #600](https://github.com/alibaba/zvec/pull/600)。只给 solver 修复前源码和中性问题；issue 正文、修复、gold、评分标准及 Git 历史均不挂载。新仓库并不能证明没有进入模型训练集。本题已做源码核验；冻结的修复前版本尚未执行原生崩溃复现，另有修复后现成包的辅助检查，见下文。

Baseline 允许 Read/Grep/Glob；zg 组额外提供当前 Rust 的原生 search MCP，包括原生工具说明。两组使用相同问题、源码、模型及限额，每组最多 30 turns / 900 秒 / 单次输出 8192 tokens，顺序 baseline → zg。源码只读，HOME、索引副本和会话独立。开始计时前构建索引并用真实容器权限执行 MCP 检索预检；索引时间单列。

参考了主干的隔离、成对比较、模型配置、索引排除规则与本地 embedding，但这不是官方 SWE-QA 分数复现：agent、输出限制、重复次数与 judge 均有差异。Claude 的 `high` 也不宣称等价于主干 OpenCode 的 Qwen `high/xhigh`。

质量评分由隐藏 arm 标签的独立 Codex 模型进行，逐项核对冻结源码；不是人类评分，也不是 SWE-QA 官方 evaluator。reflex-6 使用源码纠正后的 5 项 rubric，zvec-q001 使用 7 项 rubric。均要求至少 8/10、关键因果项满分、无关键矛盾及有效源码证据。

## 协议与结果保留

- V1：[39272396793](https://github.com/Cuiyus/zvec-grep/actions/runs/39272396793)，执行 commit `fac3c96c`，两条路线各一 pair。
- Root V2：[39275214277](https://github.com/Cuiyus/zvec-grep/actions/runs/39275214277)，执行 commit `87a8a1c1`，仅 reflex-6 一 pair。
- Completion V2：[39278383394](https://github.com/Cuiyus/zvec-grep/actions/runs/39278383394)，执行 commit `a5d9f1ed`，仅 zvec-q001 一 pair。
- Completion V2 确认：[39282211793](https://github.com/Cuiyus/zvec-grep/actions/runs/39282211793)，执行 commit `1b541847`，同一 zvec-q001 两 pair，分别 zg→baseline、baseline→zg。

V1 reflex-6 的 zg 调用了 `root="/"`，得到缺失索引错误，然后使用原生工具作答。V2 因此给**两组**共同追加一条说明：仓库根路径固定为 `/workspace`，所有仓库 root 参数使用此值。其余题目、rubric、检索实现和限额不变。V2 是观察首轮问题后的开发迭代，与 V1 分开报告，不替换首轮，也不能当作独立预注册的确认实验。

## 观测结果

| 协议 / case | 组别 | 质量 /10 | 达到源码质量门槛 | Input tokens（含 cache） | Output tokens | 工具调用 | Agent wall | zg 成功 / 尝试 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| V1 / reflex-6 | baseline | 10 | 是 | 69,874 | 3,379 | 14 | 91.56 s | — |
| V1 / reflex-6 | zg | 10 | 是 | 60,939 | 3,260 | 9 | 90.51 s | 0 / 1 |
| Root V2 / reflex-6 | baseline | 9 | 否：C5 未满分 | 46,575 | 2,240 | 10 | 66.27 s | — |
| Root V2 / reflex-6 | zg | 10 | 是 | 101,549 | 2,928 | 9 | 82.56 s | 1 / 1 |
| V1 / zvec-q001 | baseline | 未完成 | 否 | 1,979,719 | 19,606 | 44 | 755.71 s | — |
| V1 / zvec-q001 | zg | 未完成 | 否 | 不完整，不比较 | 不完整，不比较 | 54 | 900.11 s | 1 / 1 |
| Completion V2 / zvec-q001 | baseline | 9 | 是 | 590,565 | 17,134 | 22 | 573.52 s | — |
| Completion V2 / zvec-q001 | zg | 9 | 是 | 287,849 | 15,687 | 20 | 467.15 s | 1 / 1 |
| 确认 r02 / zvec-q001 | baseline | 8 | 是 | 230,055 | 11,946 | 24 | 376.15 s | — |
| 确认 r02 / zvec-q001 | zg | 8 | 是 | 479,220 | 18,249 | 21 | 553.79 s | 1 / 1 |
| 确认 r03 / zvec-q001 | baseline | 9 | 否：核心引用不完整 | 221,525 | 15,072 | 24 | 461.19 s | — |
| 确认 r03 / zvec-q001 | zg | 8 | 是 | 371,252 | 15,344 | 24 | 486.98 s | 0 / 1 |

索引与 MCP 预检在 agent wall 外：V1 reflex 为 6.81 s + 1.07 s，Root V2 reflex 为 7.53 s + 1.02 s；V1 zvec 为 28.05 s + 1.12 s，Completion V2 zvec 为 28.23 s + 1.17 s。容器构建与下载也不包含在 agent wall 内，不能把该列称为首次安装到答案的总耗时。

V1 reflex-6 的 input 少了 12.8%，但唯一 zg 调用失败，不能归因于成功检索。Root V2 正确检索后，input 增加 118.0%，wall 增加 24.6%，工具调用少 1 次。baseline 明确写成只有 `needs_update()` 才会重算，忽略缓存缺失这一独立分支，C5 因此只得 1/2；zg 回答正确区分了两者。这是 **9→10 分的质量提升与更多消耗之间的取舍**，不能仅用质量门槛的二值变化包装成大幅提升。

Root V2 的检索命中了 `reflex/vars/dep_tracking.py`、`reflex/vars/base.py` 和依赖初始化方法，但短片段尚未给出完整因果链，solver 仍继续查证。zg 组 Read 从 2 次增至 4 次，去重工具返回文本从 36,013 增至 50,357 字符；额外 input 中 48,880 tokens 来自 cache read，占增量约 89%。这支持上下文被反复计入的解释，不能把增量理解为同等数量的独立新内容。无法从单次运行分离检索、生成长度、服务延迟与提示开销各自对 wall 的因果贡献。

因此，仅把 SWE-QA agent 换成 Claude Code + Qwen，本次还没有得到明显且稳定的效率收益。路径说明解决了可用性问题；若继续开发，应把“取回足够精确证据、减少重复阅读”作为可测机制，而不是更换题目直到数字好看。

zvec baseline 用尽 30 turns，zg 在 900 秒被终止；两个答案文件均为 0 字节，不把过程文本补成答案评分。两组协议有效、源码未变、密钥扫描通过，zg 的唯一搜索使用正确 root，成功将 `SegmentImpl::recover` 排第一。两次 treatment 工具错误均为原生工具猜错文件路径，非 MCP 故障。

逐项核对实际工具返回后，baseline 在第 11 个工具轮／第 14 次调用已取得 C1–C3 与 repair 的核心证据，zg 在第 12 轮／第 21 次调用已取得；zg 到第 21 轮取得 Stats 证据。随后两组继续追查向量合并、版本元数据、WAL 底层与测试。最终 44/44、54/54 个工具调用均有返回，zg 超时前没有挂起工具。因此可观察的瓶颈是取证后未收敛并交付，不能归因于找不到代码。中间推理不作为 QA 得分。

zg 的原始 metrics 保留了 fallback 的 `139,644 input / 0 output`，但缺少最终 result 和 cache usage，已有生成内容也说明 0 不是实际零输出。汇总将其标为不可比较，不计算相对 baseline 的 token 降幅。`summary.all_planned_trials_completed=true` 仅表示两个执行都结束，不表示任务完成。

Completion V2 只改变两组共同的取证预算说明与 hook：最多准入 24 次工具调用、600 秒后拒绝新的取证，总限额仍为 30 turns／900 秒。阈值是在看过 V1 轨迹后选定的，详见[冻结计划](../../experiment-completion-v2.json)。题目、源码、gold、模型和检索实现均保持不变。真实容器离线探针验证了拒绝执行和后续作答机制，但本次实际 QA 两组都在预算内自行结束：22／20 次准入，最后一次分别为 354.40／344.46 秒，均没有拒绝、错误或挂起调用。因此不能声称是强制截断导致了完成。

新一轮匿名源码评分均为 9/10，三个关键因果项均满分，失分点不同：baseline 缺回归测试的无崩溃对照，zg 未解释 index completeness 来自元数据。两组还有不影响该冻结门槛的次要错误：zg 错误描述了向量合并读取恢复后内存索引，baseline 的仅 Flush 后重开应计数为零的预测不成立；两组对 Optimize 后同句柄 Fetch 的概括也过强。主修复原则获分，未经证明的 guard-only 备选修复不获支持。**9/10 和通过门槛不代表每一句都正确**，完整判断见[盲审](zvec-q001-completion-v2/blinded-review.json)。

本 pair 的 input（含缓存）590,565→287,849（−51.26%），output 17,134→15,687（−8.45%），工具调用 22→20，wall 573.52→467.15 秒（−18.55%）。减少的 302,716 input 中，266,046 来自 cache read 的减少；这不是经账单验证的费用降幅。两组都是完整会话 usage 和真实最终答案，不使用超时 fallback 制造节省。

zg 在第 2 次调用使用 `/workspace` 搜索，将 `recover()` 排在第一；短预览并未给齐恢复因果链，后续 Read 才补全。两组 Read 都为 10 次，但工具返回字符数 198,991→71,974，重复读取源码行仅 11→3，因此较强的可观察机制是**读取范围更集中**，不是消除了大量重复调用。完整 C1–C3 源码在 baseline 第 16 次调用／第 12 个工具消息轮、zg 第 17 次调用／第 9 轮到齐；“轮”按含工具的消息定义，不能等同于 SDK num_turns。zg 没有取回完整 Stats 证据，和其 C4 失分一致。这些轨迹关联不构成单次实验的因果证明。

GitHub 元数据曾在实际运行期间持续显示 queued；此前的[排队观测](zvec-q001-completion-v2/queue-observation.json)保留。完整产物及 15:52:11 UTC 的 job 清理日志确认执行结束，详见[执行记录](zvec-q001-completion-v2/execution.json)。没有因为状态延迟取消或重新提交此 pair。

两对确认按[冻结计划](../../experiment-completion-confirmation.json)执行，四次均交付真实最终答案和完整 usage；独立核验确认顺序、源码、模型、隔离与密钥扫描符合要求。准入分别为 21、24、24、24 次，拒绝均为零，不能归因为强制截断。索引 28.81 秒、MCP 预检 1.27 秒单列于 agent wall 之外。实际 job 在 16:33:54 UTC 完成清理与产物发布；GitHub 顶层状态仍有延迟，完整[执行证据](zvec-q001-completion-confirmation/execution.json)保留，未取消或重投。

新盲评的匿名答案 H/J/K/L 分别为 8/8/9/8 分。解盲后，r02 baseline/zg 都通过；r03 baseline 的事实因果正确，但未给出支持 `internal_insert` 写入 memory/index/doc_ids_ 的核心源码引用，因此 9/10 仍未通过证据门槛，zg 8/10 通过。四份均遗漏 metadata-derived index completeness；r02 两份及 r03 zg 的测试没有完整比较原始文档／向量内容，r03 zg 也遗漏必要对照。无冻结规则列出的关键矛盾，次要错误仍完整列在[盲审](zvec-q001-completion-confirmation/blinded-review.json)，评分通过不代表每句正确。

r02 zg input 增加 108.31%、wall 增加 47.23%；r03 分别增加 67.59%、5.59%。r02 成功用 `/workspace` 检索并定位 recover，但 Read 7→12、工具返回文本 97,632→132,425 字符。r03 的唯一 MCP 调用使用 `/`，返回 `ZG.ENGINE.NOT_FOUND`，随后靠原生工具作答；这是保留在有效试验内的参数错误，不是基础设施无效试验。两次 token 增加都不能解释成检索节省。完整[判定](zvec-q001-completion-confirmation/confirmation-verdict.json)为 `signal_reproduced=false`；不与选中的开发 pair 合并。

基于四次完成但效率信号未复现的结果，另行[冻结两个已选 reserve issue 的小批次](../../issue-expansion-decision-v1.json)：q002、q003 题目及 rubric 均未根据本次结果修改，沿用相同 completion-v2，保留两题全部结果。这用于探索任务差异，不假定 zg 已有优势，也不把同题重复包装成新题泛化。

## 新增 issue 小批次

[run 39287659748](https://github.com/Cuiyus/zvec-grep/actions/runs/39287659748) 在 commit `5acff36a` 执行预先选定的 q002、q003，各一对 baseline→zg。两题均完成执行审计与新匿名评分，四份最终答案、完整 usage 和全部不利结果均保留，不与 q001 开发或确认结果合并。冻结的[整组判定](issue-expansion-v1/set-verdict.json)为 `set_signal_rule_met=false`。

| 题目 | 组别 | 质量 /10 | 达到源码质量门槛 | Input（含 cache） | Output | 工具调用 | Agent wall | zg 成功 / 尝试 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| q002 / 零匹配标量过滤与全文检索 | baseline | 10 | 是 | 229,847 | 9,182 | 21 | 303.74 s | — |
| q002 / 零匹配标量过滤与全文检索 | zg | 10 | 是 | 420,136 | 12,289 | 24 | 413.54 s | 2 / 2 |
| q003 / 首次 HNSW 构建与删除后的行号错位 | baseline | 6 | 否 | 399,254 | 12,362 | 24 | 448.75 s | — |
| q003 / 首次 HNSW 构建与删除后的行号错位 | zg | 9 | 否 | 544,094 | 19,433 | 23 | 656.74 s | 1 / 1 |

q002 两组均通过，zg input 增加 82.79%、wall 增加 36.15%，没有评分提升。Read 从 9 次增至 14 次，工具返回文本从 106,091 增至 121,993 字符。两组各有 5 项非关键事实或引用问题，10/10 不代表附加内容全部正确。例如 baseline 的一个“只保留 invert_filter_”备选修复不足，zg 对 vector 路径表示和额外 NOT IN 测试分类的说法不准确。主因果解释、有效主修复和要求的测试矩阵均满足冻结 rubric；细节保留在[完整盲审](issue-expansion-v1/q002/blinded-review.json)，没有修改标准以追求分差。

q003 的事实分数从 6 提升至 9，但两组都未通过。zg 正确解释了删除过滤、向量密集重编号和保留原物理行号造成的错位，却遗漏 C2 要求的 FLAT memory 向持久 vector indexer 的 flush 转移，并且 C3 对 reducer 的引用只有文件名，没有支持密集编号的行号，因此不能把 9/10 当作源码质量通过。baseline 还缺少低删除比例分支实际传入 filter 的调用点及保留物理 BlockMeta 的解释，并混淆既有索引控制中的跳过／复用路径。完整评分、核心引用门槛和附加错误见[盲审](issue-expansion-v1/q003/blinded-review.json)；均无已证实的冻结规则所列关键矛盾。

q003 的 zg input 增加 36.28%、wall 增加 46.35%。总工具调用 24→23、工具调用轮次 16→13，但 Read 6→8、工具返回文本 120,701→133,047 字符；较少调用没有转化为较少输入。两题共 3 次 MCP 调用均成功且使用 `/workspace`，成功检索本身不能证明效率收益或质量通过。两题均无预算拒绝；q003 最后一次工具准入仍在 600 秒研究预算内，最终答案结束于 656.74 秒符合 900 秒总限额，不能视为强制截断。

索引／预检分别为 q002 的 17.69／1.42 秒、q003 的 30.22／1.27 秒，均在 agent wall 之外。每题仅一个 baseline 先行的 pair，顺序、服务和缓存影响未被独立识别；这两道有目的选择的 issue 不能支持统计显著性、未受训练污染或总体泛化结论。两题均未达到单题信号规则，整组不满足冻结规则，不筛掉任何题目。

- [q002 单题判定](issue-expansion-v1/q002/pair-verdict.json)、[执行审计](issue-expansion-v1/q002/independent-audit.json)、[轨迹审计](issue-expansion-v1/q002/trajectory-audit.json)
- [q003 单题判定](issue-expansion-v1/q003/pair-verdict.json)、[执行审计](issue-expansion-v1/q003/independent-audit.json)、[轨迹审计](issue-expansion-v1/q003/trajectory-audit.json)、[解盲输入核验](issue-expansion-v1/q003/review-input-verification.json)
- [两题完整判定](issue-expansion-v1/set-verdict.json)

## 远端 Qwen embedding 单题验证

[run 39291972422](https://github.com/Cuiyus/zvec-grep/actions/runs/39291972422) 使用 commit `af244e04`，按[独立冻结计划](../../experiment-remote-embedding-v1.json)执行一对原 q001。相对于 completion-v2，唯一改变的因素是 embedding 整套配置：换用主干支持的 `qwen/qwen3.7-text-embedding` 及其远端服务、授权和查询等待。题目、源码、rubric、agent、原生 Rust、工具 schema、提示和预算均保持不变。

| 组别 | 事实分 /10 | 达到源码质量门槛 | Input（含 cache） | Output | 工具调用 | Agent wall | zg 成功 / 尝试 |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 9 | 否，C3 核心引用缺失 | 262,149 | 9,421 | 24 | 284.16 s | — |
| zg | 8 | 是 | 329,622 | 13,989 | 22 | 422.41 s | 1 / 1 |

两组均完成，usage 完整，工具无错误、预算无拒绝。zg input 增加 **25.74%**、wall 增加 **48.65%**。[冻结联合规则](remote-embedding-v1/pair-verdict.json)未通过：两份答案没有同时通过，zg 事实分低于 baseline，input 也未降低。数据集主指标与各分项仍单独报告：zg 的核心引用更完整，但这不是事实分全面改善或效率收益。

盲评中 baseline 正确描述了恢复和持久化的因果过程，却没有引用旧 segment 的 `Open` 如何按 writing-block 元数据决定调用 `recover`，因此 C3 引用门槛失败。zg 给出了这条边的引用，但缺少 `index_completeness` 如何从 metadata 推导的说明，失去 C4 一分。两组都未在回归步骤中比较已知文档内容和向量，C7 均失分。双方备选“只改 guard”的修复说明仍存在 WAL 清理问题，所有非关键错误保留在[完整盲评](remote-embedding-v1/blinded-review.json)；高分或通过不等于全部附加说法正确。

真实远端索引身份为指定 Qwen 模型、endpoint 和 1024 维；hybrid 和 vector-only 预检均返回有效结果，QA 中的一次调用也成功使用 `/workspace`。镜像 provenance 中仍列出用于离线 smoke 的内置 Potion cache；实际本轮索引和查询模型以 [index identity](remote-embedding-v1/embedding-index-identity.json)、[setup](remote-embedding-v1/setup.json)、[MCP preflight](remote-embedding-v1/mcp-preflight.json) 和试验命令为准，不能把镜像包含的模型当作实际使用的模型。

授权耗时 **0.365 s**，索引和 readiness **151.00 s**，整个预检 **3.72 s**，都在 agent wall 之外。预检的 hybrid / vector-only 搜索分别为 1.815 / 0.584 s；这些数值包含原生处理和网络等待，并非纯网络延迟。QA 中的查询等待已计入 zg wall。没有经过验证的 embedding 账单或不可变权重版本证明，不报告合并美元成本。

[工具轨迹](remote-embedding-v1/trajectory-audit.json)显示调用数 24→22、工具消息轮次 17→12，但 Read 6→11，Read 返回文本 34,220→70,112 字符，全部工具文本 75,722→93,390 字符。MCP 第 6 名预览包含空 WAL guard，未直接返回 `SegmentImpl::recover` 函数体；随后仍需更多读取。zg 获取保守完整证据跨度不代表已经正确使用，baseline 没有读取完整 curated span 也不自动构成答题失败。增加的上下文与 input 上升一致，不能从单 pair 推断 embedding 的因果效应。

逐消息 usage 不完整，未用于推算总账；完整 reported 用量以终端 `result.usage` 为准。8192 是配置的每请求输出上限，不能从会话输出总量或模型能力 metadata 推断每次实际请求。

这是同一已观察题目上的一个 baseline 先行 pair，历史 local 结果不是同期 embedding 对照。远端链路可用性已验证；稳定收益、模型替换的因果收益及新题泛化均未建立。[独立执行审计](remote-embedding-v1/independent-audit.json)完成 64 项核对，未发现执行失败；[匿名输入核验](remote-embedding-v1/review-input-verification.json)已确认原始、匿名和保存的答案字节一致。

## 所有前置尝试

| Run | 结果与处理 |
| --- | --- |
| [39268173808](https://github.com/Cuiyus/zvec-grep/actions/runs/39268173808) | provider 协议与精确模型探测通过；不是 QA pair |
| [39268696236](https://github.com/Cuiyus/zvec-grep/actions/runs/39268696236) | `--bare` 隐藏 Grep/Glob，取消；无有效 pair |
| [39269284319](https://github.com/Cuiyus/zvec-grep/actions/runs/39269284319) | 用户明确使用当前 Rust 后停止旧 Node 路线；不纳入效果比较 |
| [39270770023](https://github.com/Cuiyus/zvec-grep/actions/runs/39270770023) | 本地模型缓存权限阻止索引；尚未进行 QA 推理 |
| [39271151047](https://github.com/Cuiyus/zvec-grep/actions/runs/39271151047) | seed 与 trial UID 不一致导致 treatment MCP 失败；中止共享故障运行。保留已完成 reflex baseline（10/10，110,127 input tokens），不与后续不同运行拼成 pair；zvec baseline 中断，无最终答案 |

缓存权限已修复并增加非 root 离线检查；trial 与准备阶段统一 UID/GID，并在付费 QA 前增加同权限、真实索引的 MCP 预检。基础设施失败与 agent 自身工具参数错误分开记录：V1 reflex 的错误 root 属于有效实验内的 agent 行为，不剔除。

## 证据和解释边界

另用本机已有的 `@zvec/zvec 0.7.0` / macOS ARM64 做了[原生辅助检查](zvec-q001-native-validation/README.md)：150 条记录经过 SIGKILL、恢复、optimize 和新进程重开后全部保留，无崩溃对照也通过。ID、标量精确一致，向量在统一 `1e-6` 容差内一致。第一次逐位向量比较的夹具因 FP32 舍入提前失败，原始证据保留，不算故障复现或有效重复。发布元数据指向包含 #600 的源码，但不是二进制构建证明。这项检查不验证冻结的修复前提交、不参与 QA 得分，也不证明 zg 收益；精确的修复前失败／仅加入 #600 后通过仍需独立构建。

每组保存原始答案、metrics、匿名评分及解盲映射。完整 streams、命令、版本、source manifest、MCP 预检和密钥扫描在对应 workflow artifact；运行目录不上传 HOME 或 seed。原始 metrics 不回写，后补的 zg 成功调用统计另存并记录 stream/parser SHA。

- [机器可读汇总](results.json)
- [V1 reflex 证据](reflex-6/summary.json)、[盲审](reflex-6/blinded-review.json)、[解盲](reflex-6/review-mapping.json)
- [Root V2 reflex 证据](reflex-6-root-v2/summary.json)、[盲审](reflex-6-root-v2/blinded-review.json)、[解盲](reflex-6-root-v2/review-mapping.json)、[轨迹审计](reflex-6-root-v2/trajectory-audit.json)
- [zvec 证据](zvec-q001/summary.json)、[完成性检查](zvec-q001/completion-review.json)、[检索统计](zvec-q001/tool-outcomes.rederived.json)、[轨迹审计](zvec-q001/trajectory-audit.json)、[setup](zvec-q001/setup.json)
- [Completion V2 zvec 证据](zvec-q001-completion-v2/summary.json)、[盲审](zvec-q001-completion-v2/blinded-review.json)、[解盲](zvec-q001-completion-v2/review-mapping.json)、[独立完整性审计](zvec-q001-completion-v2/independent-audit.json)、[轨迹审计](zvec-q001-completion-v2/trajectory-audit.json)
- [确认试验判定](zvec-q001-completion-confirmation/confirmation-verdict.json)、[盲审](zvec-q001-completion-confirmation/blinded-review.json)、[解盲](zvec-q001-completion-confirmation/review-mapping.json)、[独立完整性审计](zvec-q001-completion-confirmation/independent-audit.json)、[轨迹审计](zvec-q001-completion-confirmation/trajectory-audit.json)

Input tokens 包含普通输入、cache read 与 cache write；优先采用会话最终 `result.usage`。Wall time 包含容器和 MCP 启动，索引及预检单列。服务返回的美元金额未经账单验证，不用于成本结论。`zg_used` 只表示尝试过；真正成功、错误及无返回调用分别统计，成功返回也不自动代表取回了有用证据。

每个开发协议每题只有一 pair；随后是同一选中题目的两对确认，以及两个预选新题各一 pair，均不能推断总体稳定性或泛化收益。更低 token 必须和答题质量、完成情况及真实检索使用共同解释，不能把超时、失败或错误调用当作效率收益。

本轮发现不完整 usage 的误读风险后，runner 增加 `usage_complete` 与 `efficiency_comparable` 标记；该修改只影响以后输出，不回写以上原始 metrics。包含超时、非零退出、缺失账单字段等回归场景的 26 项本地测试通过。
