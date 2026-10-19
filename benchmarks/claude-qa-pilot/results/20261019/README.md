# Claude Code + Qwen3.8-max QA 开发验证

本次只验证两条路线各一个 case，使用 `Claude Code 2.1.212 + qwen3.8-max`。zg 是测试时上游主干 `a424e0ce06868dc9ad8f142f57de59b8f9b58699` 的 Rust 构建，配主干默认的本地 `potion-code-16m-v2`。全部新增 CI 在 `Cuiyus/zvec-grep` 执行。

**结论：完成了可复现的最小开发验证，但还没有得到明显、稳定的 zg 效率优势。** SWE-QA 的路径说明迭代有 9→10 分的质量提升，代价是 2.18 倍 input tokens；zvec closed-issue QA 的核心代码可被召回，但两组都在交付答案前耗尽预算。新题值得继续做完成性校准，当前不能作为效果展示样本。

## 实验设计

| 路线 | 冻结 case | 源码版本 | 用途 |
| --- | --- | --- | --- |
| SWE-QA | reflex-6：computed var 的 getter、依赖分析和失效重算 | `reflex-dev/reflex@fe0f946dc0c240c6c1e513318c21db407e191c78` | 验证切换 Claude Code + Qwen 后的行为 |
| Closed-issue QA | zvec-q001：崩溃恢复后 optimize 的持久性问题 | `alibaba/zvec@ec8a78ee08b14a0b8c94158ffc1de42cd3f97f6d` | 验证跨文件因果解释的新题型 |

zvec-q001 来源于已关闭的 [issue #594](https://github.com/alibaba/zvec/issues/594) 和 [修复 PR #600](https://github.com/alibaba/zvec/pull/600)。只给 solver 修复前源码和中性问题；issue 正文、修复、gold、评分标准及 Git 历史均不挂载。新仓库并不能证明没有进入模型训练集。本题已做源码核验，未执行原生崩溃复现测试。

Baseline 允许 Read/Grep/Glob；zg 组额外提供当前 Rust 的原生 search MCP，包括原生工具说明。两组使用相同问题、源码、模型及限额，每组最多 30 turns / 900 秒 / 单次输出 8192 tokens，顺序 baseline → zg。源码只读，HOME、索引副本和会话独立。开始计时前构建索引并用真实容器权限执行 MCP 检索预检；索引时间单列。

参考了主干的隔离、成对比较、模型配置、索引排除规则与本地 embedding，但这不是官方 SWE-QA 分数复现：agent、输出限制、重复次数与 judge 均有差异。Claude 的 `high` 也不宣称等价于主干 OpenCode 的 Qwen `high/xhigh`。

质量评分由隐藏 arm 标签的独立 Codex 模型进行，逐项核对冻结源码；不是人类评分，也不是 SWE-QA 官方 evaluator。reflex-6 使用源码纠正后的 5 项 rubric，zvec-q001 使用 7 项 rubric。均要求至少 8/10、关键因果项满分、无关键矛盾及有效源码证据。

## 协议与结果保留

- V1：[39272396793](https://github.com/Cuiyus/zvec-grep/actions/runs/39272396793)，执行 commit `fac3c96c`，两条路线各一 pair。
- Root V2：[39275214277](https://github.com/Cuiyus/zvec-grep/actions/runs/39275214277)，执行 commit `87a8a1c1`，仅 reflex-6 一 pair。

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

索引与 MCP 预检在 agent wall 外：V1 reflex 为 6.81 s + 1.07 s，Root V2 reflex 为 7.53 s + 1.02 s；zvec 为 28.05 s + 1.12 s。容器构建与下载也不包含在 agent wall 内，不能把该列称为首次安装到答案的总耗时。

V1 reflex-6 的 input 少了 12.8%，但唯一 zg 调用失败，不能归因于成功检索。Root V2 正确检索后，input 增加 118.0%，wall 增加 24.6%，工具调用少 1 次。baseline 明确写成只有 `needs_update()` 才会重算，忽略缓存缺失这一独立分支，C5 因此只得 1/2；zg 回答正确区分了两者。这是 **9→10 分的质量提升与更多消耗之间的取舍**，不能仅用质量门槛的二值变化包装成大幅提升。

Root V2 的检索命中了 `reflex/vars/dep_tracking.py`、`reflex/vars/base.py` 和依赖初始化方法，但短片段尚未给出完整因果链，solver 仍继续查证。zg 组 Read 从 2 次增至 4 次，去重工具返回文本从 36,013 增至 50,357 字符；额外 input 中 48,880 tokens 来自 cache read，占增量约 89%。这支持上下文被反复计入的解释，不能把增量理解为同等数量的独立新内容。无法从单次运行分离检索、生成长度、服务延迟与提示开销各自对 wall 的因果贡献。

因此，仅把 SWE-QA agent 换成 Claude Code + Qwen，本次还没有得到明显且稳定的效率收益。路径说明解决了可用性问题；若继续开发，应把“取回足够精确证据、减少重复阅读”作为可测机制，而不是更换题目直到数字好看。

zvec baseline 用尽 30 turns，zg 在 900 秒被终止；两个答案文件均为 0 字节，不把过程文本补成答案评分。两组协议有效、源码未变、密钥扫描通过，zg 的唯一搜索使用正确 root，成功将 `SegmentImpl::recover` 排第一。两次 treatment 工具错误均为原生工具猜错文件路径，非 MCP 故障。

逐项核对实际工具返回后，baseline 在第 11 个工具轮／第 14 次调用已取得 C1–C3 与 repair 的核心证据，zg 在第 12 轮／第 21 次调用已取得；zg 到第 21 轮取得 Stats 证据。随后两组继续追查向量合并、版本元数据、WAL 底层与测试。最终 44/44、54/54 个工具调用均有返回，zg 超时前没有挂起工具。因此可观察的瓶颈是取证后未收敛并交付，不能归因于找不到代码。中间推理不作为 QA 得分。

zg 的原始 metrics 保留了 fallback 的 `139,644 input / 0 output`，但缺少最终 result 和 cache usage，已有生成内容也说明 0 不是实际零输出。汇总将其标为不可比较，不计算相对 baseline 的 token 降幅。`summary.all_planned_trials_completed=true` 仅表示两个执行都结束，不表示任务完成。

下一轮建议只保留这一个 zvec case，冻结相同 source/gold，给两组相同的调查预算与预留回答阶段，停止取证后必须用现有证据作答并标注缺口。作为新的协议保留 V1 失败，再看完成率、因果准确性与消耗。此建议尚未执行；本轮没有继续扩大样本或无限重跑。

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

每组保存原始答案、metrics、匿名评分及解盲映射。完整 streams、命令、版本、source manifest、MCP 预检和密钥扫描在对应 workflow artifact；运行目录不上传 HOME 或 seed。原始 metrics 不回写，后补的 zg 成功调用统计另存并记录 stream/parser SHA。

- [机器可读汇总](results.json)
- [V1 reflex 证据](reflex-6/summary.json)、[盲审](reflex-6/blinded-review.json)、[解盲](reflex-6/review-mapping.json)
- [Root V2 reflex 证据](reflex-6-root-v2/summary.json)、[盲审](reflex-6-root-v2/blinded-review.json)、[解盲](reflex-6-root-v2/review-mapping.json)、[轨迹审计](reflex-6-root-v2/trajectory-audit.json)
- [zvec 证据](zvec-q001/summary.json)、[完成性检查](zvec-q001/completion-review.json)、[检索统计](zvec-q001/tool-outcomes.rederived.json)、[轨迹审计](zvec-q001/trajectory-audit.json)、[setup](zvec-q001/setup.json)

Input tokens 包含普通输入、cache read 与 cache write；优先采用会话最终 `result.usage`。Wall time 包含容器和 MCP 启动，索引及预检单列。服务返回的美元金额未经账单验证，不用于成本结论。`zg_used` 只表示尝试过；真正成功、错误及无返回调用分别统计，成功返回也不自动代表取回了有用证据。

每个协议每题只有一 pair，不能推断统计稳定性或泛化收益。更低 token 必须和答题质量、完成情况及真实检索使用共同解释，不能把超时、失败或错误调用当作效率收益。

本轮发现不完整 usage 的误读风险后，runner 增加 `usage_complete` 与 `efficiency_comparable` 标记；该修改只影响以后输出，不回写以上原始 metrics。包含超时、非零退出、缺失账单字段等回归场景的 26 项本地测试通过。
