# reflex-6 只读检索 v4：真实 query、入口命中与稳定性审计

本轮已完成旧轨迹的离线抽取与评分：**相同初始模型请求、相同主 query、相同完整工具请求、相同后续行为，是四个不同层次的一致性。** 当前证据可以定位差异发生在哪一层，不能证明某种 query 改写导致总成本下降，也不能证明 zg 已实现“质量不降且 input tokens / tool calls 下降”。

**已完成：240/240 次检索成功并通过完整性校验；48 个固定请求单元各自 5 次公开输出完全一致。** 本轮没有新增生成式 LLM 调用、E2E 会话或最终答案评分；本地 embedding 正常运行。执行和计分定义见 [v4 协议](../benchmark-protocols/readonly-qa-v4.zh-CN.md)，agent 参数支持与复现边界见 [稳定性控制说明](../benchmark-protocols/agent-stability-controls.zh-CN.md)。[v3 报告](readonly-qa-v3-reflex-6-2026-09-09.zh-CN.md) 的原始结果保持不变。

## 1. 来源与观察单位

旧轨迹来自 [CI run 34255587426](https://github.com/Cuiyus/zvec-grep/actions/runs/34255587426)，执行 commit 为 `dc0c2f2a7c52cbeb127e90e9a8cfcac2b8ab81a7`。固定任务为 SWE-QA `reflex-6`，Reflex 源码 commit 为 `fe0f946dc0c240c6c1e513318c21db407e191c78`；zg 发布包为 `@zvec/zvec-grep@0.2.2`，embedding 为 `local/potion-code-16m-v2`。

三组原试验分别为 OpenCode 1.18.4 + GLM-5.2、OpenCode 1.18.4 + Qwen3.8-Max、QoderCLI 1.1.45 + Qwen3.8-Max。每组原计划为 baseline 5 次、zg 5 次，共 30 个 E2E 试验；以下 query 分析聚焦其中 15 个 zg treatment 试验，仍然只有 **1 个独立 QA 任务**。

主分析产物来自 [成功 CI 34452219365](https://github.com/Cuiyus/zvec-grep/actions/runs/34452219365) 的 [完整归档](https://github.com/Cuiyus/zvec-grep/actions/runs/34452219365/artifacts/10142209398)。精简的逐试验请求、来源哈希、全部回放指标与完整性证据已保存为 [可审阅 JSON](readonly-qa-v4-reflex-6-evidence.json)，完整 48 单元表见 [回放表](readonly-qa-v4-reflex-6-replay.md)。

| 产物 | 内容 | SHA-256 |
|---|---|---|
| `first-query-analysis.json` | 原生首批调用、参数、反馈时序、wire 身份 | `9c84676635d46153216ea61bda8a4dbce2219972d3e66c9cafe32bc8232d50d4` |
| `observed-query-scores.json` | 旧公开返回的 request-level 两列入口评分 | `899b331461e5c4bbbbb2a95767fb0e7a218c99dd1155018d97f7674b1706c0d9` |
| `replay-plan.json` | 36 个受控单元与 12 个 faithful 单元，各重复 5 次 | `ce59cd08927ce534e0d694886092a49f1ce18f03a9a66e315a4b68bac35728b5` |

本轮已找回原 OpenCode + Qwen 的完整 ZIP，SHA-256 为 `af7d205ab5657ba3a08c910a01eb44dab4f97640fa9c9ef17dedbdec712de742`，校验通过。因此本报告不再将该组列为“完整 ZIP 缺失”。恢复材料不改变原 r02 的 provider 异常与 `measurement_failure / execution_incomplete` 状态：当时存在 5 次 HTTP 500，不能因本次入口命中或存在最终答案而改为成功。

“首批”指首次出现 zg 调用的**同一条模型决策消息中的所有 zg 调用**，并非仅第一个工具事件。当前共 14 个试验实际采用 zg，产生 15 个首批 zg 调用；另 1 个试验确认没有调用 zg。参数按原始类型保存，缺省不补成默认值。下文的 batch 种数比较其中的 zg 调用序列，不把同轮其他工具从时序证据中删除。

## 2. 首轮参数实际有多一致

| 组合 | 采用 zg / 计划试验 | 首个主 query 种数 | 首个完整原始参数种数 | 首决策轮 zg 批次种数 |
|---|---:|---:|---:|---:|
| OpenCode + GLM-5.2 | 5/5 | 1/5 | 2/5 | 3/5 |
| OpenCode + Qwen3.8-Max | 5/5 | 2/5 | 5/5 | 5/5 |
| QoderCLI + Qwen3.8-Max | 4/5 | 2/4 | 4/4 | 4/4 |

种数是精确字符串或规范化 JSON 的不同取值数，分母是有观测的试验数，不是成功率。Qoder 的未采用试验保留在 5 次计划的分母内，不能转成检索未命中，也不能静默删除。

三组最常见的主 query 都是：

```text
derived state variable class computation function accessor dependency tracking recomputation
```

GLM 的 5 次主 query 完全相同，但 r04 显式传入 `limit=15`，其余首个调用省略 limit；r02 同一首决策消息另有一条 `derived state variable computation function getter`。因此“主 query 1 种”没有代表完整参数或调用批次固定。该 getter 调用与前一条 zg 属于同一模型消息，不能描述为看到前一条返回后才改写。

OpenCode + Qwen 的 r03 在 `tracking` 后增加 `state`，其他 4 次主 query 相同；5 次实际参数仍各不相同：

| 试验 | 主 query | FTS 字符串 | vector 字符串 | 显式 fuse |
|---|---|---|---|---|
| r01 | 最常见文本 | F1 | 省略 | 省略 |
| r02 | 最常见文本 | F2 | V1 | 省略 |
| r03 | 增加 `state` | F2 | V2 | 省略 |
| r04 | 最常见文本 | F3 | V3 | 省略 |
| r05 | 最常见文本 | F2 | 省略 | `true` |

这些 FTS / vector 值在原始请求中是**带括号和引号的单个字符串，并非数组**，如下保持原文；回放不替 agent 拆分或修正：

```text
F1 = ["DerivedState", "computation", "derived"]
F2 = ["computed", "derived", "dependency", "recompute"]
F3 = ["computed", "derive", "dependencies"]
V1 = ["computed signal getter dependency tracking", "derived state recompute dependencies"]
V2 = ["computed state variable getter for computation function", "dependency tracking for state recomputation"]
V3 = ["computed state variable getter returns compute function", "dependency tracking for state recomputation"]
```

Qoder r01 为 `fuse=true, limit=20`，r04 为 `fuse=true, limit=15` 并增加一条 vector 字符串，r05 为 `fuse=true`、省略 limit，r03 则省略 fuse / limit 且主 query 不含末尾 `recomputation`。r02 没有使用 zg。

Qoder r03 的首次 zg 位于第 2 个模型决策轮，前一轮两个 Grep 分别搜索 `class DerivedSnapshotState` 与 `derivedStateOf`，均已返回 `No matches found`。它是**得到先前反馈后的首次 zg 请求**，不应与其他初始轮调用视为完全相同上下文的重复。r05 的 Grep 与 zg 则属于同一模型消息；即使工具执行顺序有先后，也没有证据表明该 zg query 已利用同轮 Grep 返回。

## 3. temperature=0 与相同初始 wire 请求仍不足以固定行为

两个 OpenCode 组均已核验各自 5 次 treatment 的 `request-002`：这是首次携带非空工具目录的主 QA 请求，前面可能存在标题请求。其原始请求字节哈希各自在组内完全相同，且重建哈希与记录值一致；规范化 body、工具 schema 与工具名称也通过核对。

| 组合 | 已核验请求数 | wire `temperature` | wire `top_p` | 组内原始 wire 字节不同值数 |
|---|---:|---:|---|---:|
| OpenCode + GLM-5.2 | 5 | 0 | 字段未发送 | 1 |
| OpenCode + Qwen3.8-Max | 5 | 0 | 1 | 1 |
| QoderCLI + Qwen3.8-Max | 0 | 未核验 | 未核验 | 未知 |

```text
GLM wire SHA-256:  c3ab4463d8bbd051d3eedf7e43832e34edeb12f711e66a1edf697ee0636be18e
Qwen wire SHA-256: 44b6b7cc3ed67af860669f63640620858b3b3421d736e8ad912fbe03a752797a
共同 tools schema: 7769c3241c75a260f99cc21424ab5725207c6f5c4963c6b1b63384e7c447f4ff
```

逐试验证据保存在 `initial_request_identity`，组内规范化 body 汇总为 `initial_request_body_consistency`；原始字节相同的判断还依赖逐试验 `request_sha256_verified=true`，不能只凭规范化 JSON 相同得出。该核验仅覆盖一个 case 各 5 次初始请求，**不证明所有后续提示相同，也不证明模型别名背后的权重、服务实例或服务端处理固定**。temperature=0 不构成端到端确定性的保证；本次也没有实测依据把 Qoder 归为 temperature=0。

更直接的反例来自 GLM：r01、r03、r05 的首个 zg 完整参数相同，公开返回 SHA-256 都是 `4c51d1bf56eacf3dc46b14396908b84c06afba14417e0a05cd82a9c53498b984`，旧 E2E 分析中的首次有效入口累计 input 都是 5,289，但后续总量仍然不同。

| GLM zg 试验 | 首次有效入口累计 input | 最终累计 input | 最终工具调用数 |
|---|---:|---:|---:|
| r01 | 5,289 | 118,255 | 12 |
| r03 | 5,289 | 85,166 | 7 |
| r05 | 5,289 | 71,497 | 8 |

此处 input 延续原 `e2e-analysis.json` 口径，包含 cache-read input；不是费用换算。该观察说明固定首 query 仍远不足以稳定整个 agent 的成本，没有隔离后续差异究竟来自模型、服务端执行还是各步工具反馈。低温、固定配置与记录请求是在控制行为方差；固定任务的多次重复则是在测量剩余方差，二者不能互相替代，也不增加独立任务覆盖。

## 4. 旧返回按“本次 query 目标”与“旧 task 入口”分别评分

以下读取 `observed-query-scores.json` 的 **`request_scores`**，评分对象是完整请求的公开联合返回。多 route 请求不能把同一返回重复算作各条 route 独立成功，更不能将命中归因于其中某条文字。

query 目标来自本次开发标注 `reflex-6-query-intents-v4-dev1`；旧 task 入口仍是三个依赖主函数的 OR。前者问“这次检索目的是否定位到”，后者保留原任务依赖入口诊断。标注由本次开发 agent 在看过旧轨迹后编写，是源码核验的开发标签，**不是官方穷尽相关性 gold，也不是未见测试集**；未命中这些锚点不等于返回完全无用。

### GLM：getter 子目标命中，不应被旧 task 入口误判

| 实际请求 | query 目标首 rank | 旧 task 入口首 rank | 原生输出字节 | 到 query 命中块末的累计字节 |
|---|---:|---:|---:|---:|
| 5 次首个主 query | 5 | 5 | 6,520；r04 为 8,775 | 3,332 |
| r02 同轮第二条 getter query | 4 | 未命中 | 6,733 | 2,409 |

getter query 的第 4 项公开显示 `ComputedVar.fget`，包括 `return self._fget`，符合“定位计算函数 accessor”这一合法子目标；旧 task 的依赖主函数未出现，因此两列一中一未中是符合各自定义的结果。它不表示 zg 检索失败，也不表示 getter 一段已足够回答完整原题。

### OpenCode + Qwen：完整请求不同，联合返回排名与字节窗口随之不同

| 试验 | query / task 首 rank | task Hit@10 | 到原生命中块末累计字节 | 原生总字节 | 前 4,096 字节可见入口 rank | 前 8,192 字节可见入口 rank |
|---|---:|---|---:|---:|---:|---:|
| r01 | 9 | 是 | 6,004 | 12,276 | 未见 | 9 |
| r02 | 6 | 是 | 4,010 | 12,713 | 6 | 6 |
| r03 | 1 | 是 | 1,190 | 13,076 | 1 | 1 |
| r04 | 13 | 否 | 8,770 | 13,733 | 未见 | 13，块被截断 |
| r05 | 6 | 是 | 3,702 | 6,370 | 6 | 6 |

保留原输出全部 slot，才看得到 r04 的第 13 项；它的 `Hit@10=false, RR@10=0`，不能删除前项或把完整输出统统截成 10 项后隐藏这个现象。8,192 字节窗已显示第 13 项的完整定义锚点，但该结果块尚未显示完，因此该窗口记为入口可见，与原生完整命中块末累计 8,770 字节并不矛盾。

这 5 次同时改变了主 query 或辅助字符串、融合参数等，不是单因素实验。排名差与请求差异存在可观察关联，但不能据此断言“多一个 state”“使用某条 vector”或“query 改写”造成了排名差，更不能跨越后续阅读和生成过程推导总 input 成本的因果变化。r02 的检索评分仍不能修复其原 E2E 的测量失败。

### Qoder：采用和前置反馈必须保留

| 试验 | query 目标首 rank | 旧 task 入口首 rank | 原生总字节 | 到 task 命中块末累计字节 | 首次 zg 前已有反馈 |
|---|---:|---:|---:|---:|---|
| r01 | 5 | 5 | 11,640 | 3,332 | 否 |
| r02 | 不适用：未调用 | 不适用：未调用 | — | — | — |
| r03 | 9 | 5 | 6,446 | 3,509 | 是，两次 Grep 无匹配 |
| r04 | 6 | 6 | 9,713 | 3,501 | 否 |
| r05 | 5 | 5 | 6,520 | 3,332 | 否 |

r03 对应的 query 目标是更具体的依赖发现入口，在第 9 项、累计 6,024 字节；旧 task 的其他可接受依赖入口在第 5 项先出现。前 4,096 字节窗因此能看到 task 入口，但尚未看到该 query 的直接目标。单独展示两列避免把“提前找到桥接入口”当成“本次目标已直接出现”。

所有字节窗口都是对**同一旧公开文本的离线前缀观察**，不是旧 E2E 实际施加的工具输出预算，也不是模型 input tokens。累计字节通常计算到命中结果块末尾，并非目标符号的精确 token 位置；入口可见更不等于完整答案证据已经足够。

## 5. 新建索引回放与验证状态

计划已冻结为 **12 条文本 × 3 模式 × 5 次 = 180 次受控检索，加 12 种真实完整请求 × 5 次 = 60 次 faithful 回放，总计 240 次**。12 条文本是原题加 11 种已观测字符串；不新增人工改写探针。faithful 保留真实请求类型、route、limit、fuse 和缺省字段。无采用的 Qoder r02 仍记录为不可回放的 no-call，不能为其发明请求。

索引在本轮准备阶段新建；不同回放单元使用本轮种子的独立副本，每单元 5 次串行复用同一引擎进程。固定第 1 次用于质量观察，其余重复检查公开输出和运行状态的重复性；第 1 次也不等于严格冷启动。准备成本、各次延迟、公开文本哈希、源码与索引完整性必须单独保留。

| 检查 | 当前状态 |
|---|---|
| 本地基础测试 | 已完成一轮：Python 303 tests（1 skipped），Node 20 tests 全通过 |
| 首次新 CI | [34451994169](https://github.com/Cuiyus/zvec-grep/actions/runs/34451994169)，commit `8b07966`；模型身份校验阶段失败，实际检索 0 次 |
| 首 CI 失败原因 | 新校验错误期待 Qoder 模型为 `custom-openai/qwen3.8-max`；真实 manifest 为 `qwen3.8-max`。这是协议实现错误，不是检索性能失败 |
| 身份校验修复 | 已修复并增加 1 个回归测试；真实 CLI 本地离线 analyze 已通过，产出 12 个 faithful 单元 / 240 次计划；不代表已执行 240 次检索 |
| 旧付费 workflow | [34451994259](https://github.com/Cuiyus/zvec-grep/actions/runs/34451994259) 为 Skipped |
| 修复后验证 | 本地 13 项 replay 回归通过；成功 CI 中 Python 304 tests（1 skipped），Node 20 tests 全通过 |
| 修复后的新 CI | [34452219365](https://github.com/Cuiyus/zvec-grep/actions/runs/34452219365)，执行 commit `a728669d03ac1dd5b3990ee787a322a22fbcbabf`；Success，240/240 次已评分 |
| 完整性 | 121 个被分析的旧产物文件哈希未变；源码与原始索引种子未变；48 个单元均通过开始/结束文档及向量校验 |

成功归档含 258 个文件，ZIP SHA-256 为 `bc205e0170b8d6d3589fca4b28309fbf432ccd3867d3c9cd9006113ed9f020b6`；本地下载后校验相同，重新读取 raw stdout / audit 事件计分与 CI 的 `replay-report.json` 逐项一致。该报告文件 SHA-256 为 `0238d48df4de55d53d4a6df9e29c552b75d8606cefe3731d62b495afb98439b2`。

索引构建和 preflight 共 16.38 秒，不含镜像构建与源码下载。索引包含 424 个文件、6,324 个片段和 6,324 个 256 维向量。执行镜像为 `sha256:df6b68b07accfef257ccf2398b1d481984708be1c353a7e9e2b521a69feb9a7d`；完整文档哈希为 `522dc69cae66682abf2a527a295b2f985f4e67234c1f8c0814aa75bb013e3182`，向量哈希为 `8a38a7f5d4ddd995d88d5dfe2b3fca062db8bcc9cd608cf26815b33c4f8a8b17`。本地 embedding 权重哈希等记录在精简 JSON。源码只读。96 次开始/结束比对均显示副本物理存储与冻结快照不同，但文档和向量语义校验通过；这不是“索引所有物理字节稳定”，也不能把差异归因于某条查询或未经调查解释成无害元数据。原始种子未变。

### 固定检索可以重复，改写和模式选择仍显著影响入口位置

下表为受控 `limit=10` 的第 1 次结果，数字是 query 目标排名；每格另外 4 次均与该次公开输出字节一致。

| 文本 | FTS | vector | hybrid | hybrid 前 4 KiB 是否显示目标 |
|---|---:|---:|---:|---|
| 原题 | 未命中 | 6 | 6 | 否 |
| 最常见改写，结尾为 `dependency tracking recomputation` | 10 | 4 | 5 | 是 |
| Qwen r03 主改写，结尾为 `dependency tracking state recomputation` | 8 | 3 | 1 | 是 |
| GLM getter 子目标 | 2 | 未命中 | 4 | 是 |

这组对照在相同索引、模式和预算下改变文本，可以直接观察检索对不同表达的响应。原题 hybrid 的首依赖入口块结束于 4,513 字节，4 KiB 窗口中尚不可见；最常见改写相应为 3,332 字节、rank5；Qwen r03 的主文本单独 hybrid 为 748 字节、rank1。最后一项不同于其原请求联合返回的 1,190 字节，不把单文本与多 route 请求混报。getter 查询中，FTS 可以直接定位目标，vector 前十未命中；因此不能预设语义检索在所有改写上都占优。

| 模式 | query 目标 Hit@10 / 有可评分目标的文本 | 歧义文本（另保留） | 每单元首次调用耗时中位数 | 后四次调用耗时中位数 |
|---|---:|---:|---:|---:|
| FTS | 9/11 | 1 | 216.0 ms | 213.5 ms |
| vector | 8/11 | 1 | 335.2 ms | 215.8 ms |
| hybrid | 10/11 | 1 | 341.1 ms | 220.9 ms |

11 条可评分文本包含原题、等价改写和不同子目标，来自同一个 case；这张表只是本开发集合的描述，不是 11 道题的总体召回率。歧义文本的三个模式均保留 query 质量 unknown。检索耗时是 runtime.search 内的观察，不包含每单元进程启动、全部索引校验和关闭；首次调用也不是严格冷启动。

### 原请求回放与旧 E2E 返回逐字一致

12 种 faithful 请求对应旧 E2E 的 15 个首批调用。**新索引回放第 1 次的公开返回与这 15 个旧调用全部 SHA-256 相同（15/15）**，两列目标排名也一致；每个 faithful 单元自身的五次输出亦相同。新索引因此复现了本报告第 4 节的 getter 命中、Qwen rank13 等现象，而不只是重新评分同一份旧文本。

这支持将本 case 的首轮差异具体定位到“agent 选择了哪些请求及其参数”，而不是把它统称为检索黑盒：这些已记录请求的结果在本次复跑中可以重现。它不保证所有将来的索引构建或所有请求确定，也不能说明首次请求稳定以后，后续 agent 阅读与回答会稳定；GLM 相同首请求之后的总成本差异仍存在。

执行后的整理只格式化了两个新 JS 文件和 query 标签 JSON，后者与 CI 内冻结标签逐字段相同；历史 CI 的文件字节哈希和执行 commit 继续保留，不用整理后的新文件冒充原始执行产物。源码定义、标签语义、请求集合、计分和运行逻辑均未因结果改变。后续整理提交不会再触发模型或检索。

本次结果仍属于同一个开发任务的固定请求观察，不能替代新任务上的 E2E 答案质量和成本验证。
