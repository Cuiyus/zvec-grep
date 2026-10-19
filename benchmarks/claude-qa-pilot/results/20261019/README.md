# Claude Code + Qwen3.8-max QA 开发验证

本次只验证两条路线各一个 case，使用 `Claude Code 2.1.212 + qwen3.8-max`。zg 是测试时上游主干 `a424e0ce06868dc9ad8f142f57de59b8f9b58699` 的 Rust 构建，配主干默认的本地 `potion-code-16m-v2`。全部新增 CI 在 `Cuiyus/zvec-grep` 执行。

**结论：此前单题开发 pair 的效率信号没有在两对确认试验中复现。** 开发 pair 的 zg input 曾少 51.3%、wall 少 18.5%，两组均为 9/10 且通过；随后冻结的两对确认试验中，zg input 分别增加 108.3% 和 67.6%。第 2 对两组均为 8/10 且通过；第 3 对 baseline 为 9/10，但核心引用不完整而未通过，zg 为 8/10 且通过。四次均完成，不能宣称稳定效率优势。SWE-QA 有效检索迭代仍是质量 9→10 分、input 增加 118% 的取舍。所有开发失败与不利结果保留。

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

每个开发协议每题只有一 pair；随后只有同一选中题目的两对确认，不能推断总体稳定性或泛化收益。更低 token 必须和答题质量、完成情况及真实检索使用共同解释，不能把超时、失败或错误调用当作效率收益。

本轮发现不完整 usage 的误读风险后，runner 增加 `usage_complete` 与 `efficiency_comparable` 标记；该修改只影响以后输出，不回写以上原始 metrics。包含超时、非零退出、缺失账单字段等回归场景的 26 项本地测试通过。
