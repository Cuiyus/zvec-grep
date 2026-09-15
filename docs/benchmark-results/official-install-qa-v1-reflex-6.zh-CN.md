# reflex-6：zg 0.2.2 官方安装重测

本轮使用 **zg 0.2.2**，通过 `zg install --target opencode --yes`／`zg install --target qoder --yes` 集成。原样保留安装器生成的 MCP 启动命令和 guidance；没有自定义 MCP 桥接或手工提示词注入。

这是单个开发用 QA 的重复实验。当前数据不能证明「答案质量不下降且稳定节省 input token」：GLM 组平均成本降低但各次波动较大；OpenCode＋Qwen 没有采用 zg；Qoder＋Qwen 的平均 input token 增加。不能将三组简单合并成一个 zg 收益百分比。

## 1. 本轮身份与样本

- [CI 34922580478 / attempt 1](https://github.com/Cuiyus/zvec-grep/actions/runs/34922580478)，执行提交 `034cb2823341fdce3eb0751d78f3291a104edcca`，分支 `dev/benchmark-readonly-qa`。
- zg：`@zvec/zvec-grep@0.2.2`；embedding：`local/potion-code-16m-v2`；OpenCode：1.18.4；QoderCLI：1.1.45。
- QA：SWE-QA `reflex-6`；源码：Reflex `fe0f946dc0c240c6c1e513318c21db407e191c78`。原题询问 getter 如何参与依赖跟踪与状态重算，涉及 `vars/base.py`、`vars/dep_tracking.py`、`state.py`。
- 沿用已选择的开发样本与[选择记录](../../benchmarks/swe-qa-bench/cases/SELECTION.md)。本轮没有根据新结果换题；这是反复调试过的公开题，不能用于证明未见仓库上的泛化收益。
- 三组各 baseline 5 次、zg 5 次，共 **30 次新的原生 E2E**。独立会话、按预定顺序交错；保留所有结果，不挑最好一次，也不使用旧桥接实验作对照。
- 源码只读，zg 每次新建索引，原生 freshness 保持默认。安装／索引准备开销与 QA 模型成本分列；回放另建同配置索引，不宣称与所有 E2E 使用同一个冻结索引。
- [固定协议](../benchmark-protocols/official-install-qa-v1.zh-CN.md)、[固定 QA](../../benchmarks/swe-qa-bench/cases/reflex-6.json)、[运行前冻结的答案评分文件](../../benchmarks/swe-qa-bench/cases/reflex-6.judge-official-v1.json)。

## 2. E2E 成本与实际采用

以下均为全部 5 次的算术平均；变化为 `zg 均值 / baseline 均值 − 1`。input 是累计 QA 输入，包含重复上下文与 cache read；tool call 是 QA 全过程工具调用总数。

| Agent＋Model | baseline → zg input | input 变化 | baseline → zg tool calls | calls 变化 | zg 实际采用 |
| --- | ---: | ---: | ---: | ---: | --- |
| OpenCode＋GLM5.2 | 137309.8 → 123885.8 | −9.78% | 14.6 → 10.8 | −26.03% | 5/5，会话内合计 12 次 |
| OpenCode＋Qwen3.8-max | 155454.2 → 137903.4 | −11.29% | 15.4 → 14.2 | −7.79% | **0/5，合计 0 次** |
| Qoder＋Qwen3.8-max | 170217.2 → 191611.0 | **+12.57%** | 15.4 → 14.2 | −7.79% | 5/5，合计 5 次 |

GLM 的 5 对 input 仅 2 对下降，另外 3 对上升；各次 zg input 为 99754、157392、115766、117715、128802。均值降低不等于每次都节省。OpenCode＋Qwen 的工具、官方 guidance 均已加载，但 5 次都仅使用原生检索／阅读工具；因此这组成本变化不能归因于 zg 召回。Qoder 的调用数略降，但整个 QA 的累计 input 没有降低。

OpenCode 原生日志与模型请求记录逐次核对：20 次 input 均一致，275 个工具调用 ID 与参数均一致，12 个 zg 返回文本与后续模型所见文本逐字一致。每次另有一个辅助标题请求，GLM 输入 586、Qwen 输入 647；主表按原生 QA 口径不含标题，机器数据单列该开销。任务请求温度为 0；标题请求温度为 0.5，不能称全部模型请求均为零温度。

### Qoder 外层失败的离线更正

原始 CI 保留 **20 completed、10 failed**；原生会话全部 30 completed。Qoder 的 10 次外层失败来自基准脚本过严的配置字节检查：1.1.45 启动时自动加入三个默认 `securityScan` 开关，均为 `true`，并去掉 JSON 尾换行。模型、权限、MCP、guidance 未变。

根据发布版源码重建完整最终配置后，10 次最终 SHA256 全部精确匹配原记录。更正只移除这一已证实的执行门禁误报，复用原始 judge 输出与校准结果；不重跑模型、不替换答案、不修改原工件，不把原 CI 改称全绿。配置校验修复仅接受固定版本的这项原生初始化；其他控制项变化仍为 contract failure。

## 3. 答案质量：原评分与源码审核分开

| 组合 | baseline 原始双评审共识 | zg 原始双评审共识 |
| --- | --- | --- |
| OpenCode＋GLM5.2 | 5 pass | 5 pass |
| OpenCode＋Qwen3.8-max | 3 pass，2 disagreement | 4 pass，1 disagreement |
| Qoder＋Qwen3.8-max | 5 pass | 4 pass，1 disagreement |

Qoder 原始生效分数仍是 10 个 `execution_incomplete`；上表明确展示执行门禁之前的原始双评审共识，更正后的共识另存。所有评审先通过固定的四个校准样例，但校准并未消除判卷错误。

逐份核对答案与源码，发现 raw pass 仍可能遗漏必答链路或放过错误。例如 GLM 的 zg-r01、zg-r05 没有明确解释缓存失效后在访问时重新调用 getter；Qoder 的 baseline-r03、baseline-r04、zg-r04 声称分析异常后「只剩静态依赖」，实际代码可能保留已经原地添加的部分自动依赖，三份却都得到双 pass。Qoder zg-r03 还混淆了依赖定义方与计算属性消费方的状态名。

评审也会误拒绝：Qoder zg-r02 的一个拒绝理由认为源码没有调用 `defining_state._mark_dirty`，实际调用就在评审截取片段之后；该答案仍有另一项真实的 static-only 错误。这说明应同时保留答案、judge 理由和完整源码位置，不能只汇总 pass 比例。

上述审核不追溯修改本轮评分标准，也不把可选细节缺失当作新增失败条件。它足以说明：本轮双模型判卷结果还不能作为「质量不下降」的可靠证明。

## 4. 查询变化与 retrieval-only

从 30 次原生日志收集全部 **17 次实际 zg 调用**，加上固定原题请求，去重得到 **14 个完整请求、14 个标注上下文、12 个字面查询文本**，没有不可回放参数。去重依据包含工具名称和完整参数，不能将同一 query 的不同 `limit`／模式请求混为一个。

| 组合 | 首 query 文本众数占比 | 首完整请求众数占比 | 首批次完整请求众数占比 |
| --- | ---: | ---: | ---: |
| OpenCode＋GLM5.2 | 4/5 | 3/5 | 1/5 |
| OpenCode＋Qwen3.8-max | 不适用：未调用 zg | 不适用 | 不适用 |
| Qoder＋Qwen3.8-max | 2/5 | 2/5 | 2/5 |

众数占比只描述本轮最多见的请求出现几次，不代表整个行为轨迹稳定。OpenCode 两组各自 zg 条件下的首个任务模型请求体在 5 次间完全一致，GLM 的改写和同轮并行请求仍有变化。Qoder 原生流未公开完整系统提示词，该项保持未知。

更直接的后续行为差异：GLM r02/r03/r04 首个完整 zg 请求和返回文本相同，总 input 却分别为 157392、115766、117715；Qoder r03/r04 的 zg 请求和返回相同，总 input 为 149947、221833。不能将这些差异都归因于召回结果，也不能仅凭同一条首 query 判定 Agent 稳定。

查询标注由三组分别提出源码正例，再交叉审查并校验固定提交上的源码范围；不把原 QA 答案直接当作所有子 query 的 GroundTruth。标签属于部分正例，未确认的意图保持 unknown。对每个唯一请求按原样参数调用官方 MCP 5 次，固定第 1 次计算入口 Hit@1/5/10、首命中排名、RR@10，全部 5 次用于可见文本 SHA256 一致性检查；不挑最高分，不计算已弃用的字节／窗口指标。

这里的命中是「已确认源码入口是否出现在输出中」，不是完整 Recall、证据充分性或答案正确率。不同子 query 的正例集合可能不同，不能单凭 RR 更高认定 query 更优。回放是同一 MCP 会话与新建索引中的连续请求；文本重复不能推广为独立构建稳定或整个 Agent 行为稳定。

实际结果如下。70/70 次回放均完成；14 个请求的 5 次可见文本、原生 `result.content` 及完整 MCP result SHA256 均各自一致。该结论限于同一 MCP 会话和同一次新索引构建。

| 评估对象 | 可评分 / 总数 | Hit@1 | Hit@5 | Hit@10 | 平均 RR@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 新索引固定回放的 request＋context | 13/14 | 0/13 | 11/13 | 13/13 | 0.247 |
| E2E 中 Agent 实际看到的 zg 调用 | 16/17 | 1/16 | 15/16 | 16/16 | 0.271 |

原始问题采用固定 `limit=10`：首个已确认目标排第 6，Hit@5=false、Hit@10=true、RR@10=0.167。12 个可评分的 Agent 改写请求中，11 个在前 5 命中，12 个都在前 10 命中；这说明改写后的实际请求通常把已标注入口提前，但不同请求的部分正例集合并不完全相同，不能把差异全部归因于 query 改写质量。

17 次 E2E 原始输出与新索引第 1 次回放有 16 次文本完全一致。唯一差异来自 GLM r05 的 `_var_dependencies mark dirty...` 请求：同一文件中的两个 chunk 在第 1、2 名互换，实际 E2E 的首目标排名为 1，回放为 2。源码未变，回放内 5 次又完全一致；因此该差异更像独立索引构建／近似排序差异，不能归为 Agent 随机性。

GroundTruth 生成本身也被 Harness 观测：22 个标注会话中 18 个完成，2 个会话不完整，2 个 Qoder 输出无法解析为 JSON；共提出 203 个候选，94 个经交叉审查和源码验证接受，109 个保持 unknown。最终 14 个 query/context 中 13 个可评分，Qoder r05 的无 `limit` 改写因意图／正例无法可靠确认而保持 unknown。标注阶段另耗费 176 次模型请求、272 次工具调用和 5,609,687 input token，均不计入 E2E 成本。

retrieval-only 能证明的是：在固定回放条件下，zg 0.2.2 对本题的已确认入口均能在前 10 返回，结果重复性高；它也能定位原题排名靠后、一个标签未知和一次跨索引排序变化。它不能证明答案质量无损，也不能解释 Agent 获得相同检索结果后为何继续产生不同的工具轨迹与 token。GLM 和 Qoder 的相同 request＋返回仍出现明显后续成本差异，OpenCode＋Qwen 则根本没有采用 zg。

## 5. 证据与复现边界

原始 E2E、安装记录、模型请求、原生流、原始评分与诊断工件均在上述 CI 的 artifacts 中，保留期 90 天。本地核验目录为 `benchmarks/swe-qa-bench/runs/official-ci/34922580478/`。机器报告记录逐次成本、工件 SHA256、原始与更正执行状态、源码审核发现和 retrieval 结果。

这套流程已经能分开观察：安装是否正确、Agent 是否采用 zg、query 和参数如何变化、相同请求是否返回相同内容、后续行为是否继续波动，以及判卷是否有源码依据。但这是 1 个 QA 的 5 次重复，14 个请求和重复回放不增加独立 QA 样本量；不能据此承诺跨仓库稳定收益，也不能证明某条 query 导致 token 降低。
