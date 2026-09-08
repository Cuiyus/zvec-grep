# reflex-6 / zg 0.2.2：协议 v3 的执行与源码复核

三组执行、离线重新计数及逐答案源码复核均已完成。**本轮支持 GLM 组合降低检索探索成本；不支持三个组合都有稳定收益，也尚不足以证明答案质量不下降。** 成本与质量结论分开报告。

协议见 [readonly-qa-v3](../benchmark-protocols/readonly-qa-v3.zh-CN.md)。本轮 CI 为 [34255587426](https://github.com/Cuiyus/zvec-grep/actions/runs/34255587426)，执行代码 commit `dc0c2f2a7c52cbeb127e90e9a8cfcac2b8ab81a7`。原题、Reflex commit、zg 0.2.2 未变；各组新建索引，baseline 与 zg 各预定五次。

这是查看旧结果后冻结协议的单 case 开发实验，不能当作未见题集的 PR 效果评测。所有原始 CI 报告保留；后续源码复核作为单独记录，不覆盖自动 judge 的结论。

## 总览

下表变化率为 `(zg均值 / baseline均值 − 1)`；负数代表节省。每组预定 5 个 baseline / zg 时间块，三组共 30 次 QA 均已尝试，没有替换不利结果或增加样本。

| 组合 | 完整测量 baseline / zg | 平均 input 变化 | 平均 tool calls 变化 | input 更低的时间块 |
|---|---|---:|---:|---|
| OpenCode + GLM-5.2 | 5/5；5/5 | **−14.18%** | **−45.45%** | 5/5；其中两对低于 1% |
| OpenCode + Qwen3.8-Max | 5/5；4/5 | **+37.89%*** | −5.63%* | 已观察计数为 1/5 |
| QoderCLI + Qwen3.8-Max | 5/5；5/5 | **+5.92%** | −4.35% | 2/5 |

\* OpenCode + Qwen 的 r02-zg 出现 5 次 provider HTTP 500。最终有答案和已知用量，但失败请求的费用未知，因此五次均值只是已观察计数的汇总，不能当作完整的五对效果估计。即使补充观察双方均完整的四个时间块，平均 input 仍增加 22.72%。

三组分别评价，不能混合为一个总体收益率；OpenCode 和 Qoder 的 Qwen 服务路由也不同。主 CI 总耗时约 27 分钟，GLM / Qoder job 成功，OpenCode + Qwen job 因测量不完整失败。这是协议保留问题的结果，不能把 CI 成功与质量通过等同。

## Retrieval-only：可以定位入口，返回排序与长度仍有改善空间

每组 45 个预定查询，共 135 次，全部产生有效入口评分。每组内部，各 query/mode 的五次公开输出哈希一致。GLM / Qoder 已直接核对原始公开文本；OpenCode + Qwen 由原 CI 的报告与只读审计中的哈希记录支持，本地没有取得其完整 ZIP。原问题是主要质量观察，另外两条是同一问题的子意图探针；不是三个独立 case，也不把重复次数计入质量样本量。

| 查询 | 模式 | 首个函数入口排名 | 展示到入口块末尾所需字节 | 前 4096 字节内有入口 |
|---|---|---:|---:|---|
| 原始问题 | FTS | 前十未命中 | 不适用 | 否 |
| 原始问题 | vector | 6 | 3270 | 是 |
| 原始问题 | hybrid | 6 | 4513 | 否 |
| 依赖发现子意图 | FTS | 前十未命中 | 不适用 | 否 |
| 依赖发现子意图 | vector | 3 | 1810 | 是 |
| 依赖发现子意图 | hybrid | 3 | 1910 | 是 |
| 选择性刷新子意图 | FTS | 前十未命中 | 不适用 | 否 |
| 选择性刷新子意图 | vector | 5 | 3144 | 是 |
| 选择性刷新子意图 | hybrid | 7 | 5386 | 否 |

这批自然语言查询中，vector 的入口排序不差于 hybrid，且暴露入口需要的公开文本更短。FTS 的未命中限定于这些自然语言查询，不能外推成精确关键字 FTS 无效。完整九段推理证据是否直接出现在搜索输出中，不作为入口定位成败的主标准。

4096/8192 字节仅是对同一原始正文的离线预算诊断；E2E 没有截断 zg 返回。有效函数入口的定义、原始排名、源码锚点及逐次结果均在 `entry-report.json`。

三组上表的入口排名、命中字节和预算结论相同，但**跨组完整输出并非完全一致**：原问题 hybrid 的第 10 项，GLM / OpenCode+Qwen 为 FTS 返回的 `BaseHTML`，Qoder 为 vector 返回的依赖追踪测试。总输出分别为 6967 / 6886 字节，首个入口仍在第 6 项、4513 字节处。其余八种 query/mode 的公开文本哈希跨组一致。差异原因尚未确定，不能归因于 agent、模型或索引构建中的某个步骤。详见 [三组检索审计](readonly-qa-v3-reflex-6-audits/retrieval-cross-group-audit.json)。

## OpenCode + GLM-5.2：搜索阶段节省清楚，总收益幅度仍波动

| 时间块 | baseline input | zg input | baseline calls | zg calls | zg 成功调用次数 |
|---|---:|---:|---:|---:|---:|
| 1 | 118401 | 118255 | 15 | 12 | 3 |
| 2 | 130323 | 124208 | 20 | 9 | 2 |
| 3 | 114171 | 85166 | 21 | 7 | 1 |
| 4 | 109444 | 108639 | 14 | 12 | 1 |
| 5 | 119348 | 71497 | 18 | 8 | 1 |
| 均值 | 118337.4 | 101553.0 | 17.6 | 9.6 | 1.6 |
| 中位数 | 118401 | 108639 | 18 | 9 | 1 |

input 均值下降 **14.18%**，中位数下降 **8.24%**；calls 均值下降 **45.45%**，中位数下降 **50.00%**。五个时间块的两个成本指标方向都为正，但第 1 / 4 对只少 146 / 805 input tokens，第 3 / 5 对贡献约 91.6% 的总 token 节省。

不能据此说总体随机性消失：input CV 从 baseline 的 6.56% 变为 zg 的 22.10%。首次入口定位变得一致，后续读取的成本仍有明显差异。

| 成本阶段 | baseline 平均 input | zg 平均 input | 降幅 |
|---|---:|---:|---:|
| 首次命中预标注函数及发起该调用的模型轮 | 19870.4 | 5289.0 | 73.38% |
| 后续证据扩展 | 77552.0 | 75633.6 | 2.47% |
| 最终回答生成 | 20915.0 | 20630.4 | 1.36% |

约 **86.87%** 的 input 节省、**92.5%** 的工具调用节省发生在首次入口阶段。baseline 首次入口前需 `[8,6,14,8,10]` 次工具调用，zg 为 `[2,1,2,2,2]`，五次均由第一次 zg 调用给出入口。

baseline 有 17 次确认空搜索，zg 为 0。第 3 次 baseline 先搜索 TS/JS，再尝试 Python 的 derived / class Derived / computation / Derived / computation_function 等字词，发生七次空 grep。zg 的第一条语义查询直接给出了 `_deps` / 依赖注册入口。这是支持“减少猜词试错成本”的可检查轨迹证据。

阶段切分是可观测事件的记账规则，不保证模型在该点才理解问题。同一轮可能并行提出多个调用；不能把随后显示的读取都解释成看到首入口后才作出的决策。五次首条 zg 查询的文字相同，但第 4 次显式设置 limit=15，不能声称 E2E 的五次搜索参数完全相同。

## OpenCode + Qwen3.8-Max：较早定位没有转化为总 input 节省

| 时间块 | baseline input | zg input | baseline calls | zg calls | zg 测量状态 |
|---|---:|---:|---:|---:|---|
| 1 | 103418 | 142847 | 13 | 13 | 完整 |
| 2 | 78711 | 181593* | 14 | 15 | 不完整 |
| 3 | 130194 | 164310 | 15 | 13 | 完整 |
| 4 | 95974 | 158179 | 13 | 13 | 完整 |
| 5 | 152235 | 125962 | 16 | 13 | 完整 |
| 已观察均值 | 112106.4 | 154578.2* | 14.2 | 13.4 | — |
| 已观察中位数 | 103418 | 158179* | 14 | 13 | — |

五个 zg 会话都成功使用 zg 一次，全部原生工具调用均无错误。r02 的 wire 记录包含 16 个请求和 16 个响应，其中请求 3 / 4 / 5 / 6 / 8 为 HTTP 500，错误码 `BalanceError`，服务消息为 `There are no suitable services.`。服务端报告没有合适服务，具体原因未确定，不能仅凭错误码解释成账号余额不足。Agent 调用链后续重试并生成了最终答案；透明转发器本身没有添加重试。

该次保持 `measurement_failure` / `execution_incomplete`，不能因最终答出了答案而升格。完整四个时间块的补充比较同时去除第 2 块两组观测，baseline / zg 平均 input 为 120455.25 / 147824.5，仍增加 22.72%；这不是替代主要报告的筛选结果。

| 成本阶段 | baseline 平均 input | zg 已观察平均 input |
|---|---:|---:|
| 首次入口定位 | 9887.0 | 5672.0 |
| 后续证据扩展 | 79791.6 | 121824.6 |
| 最终回答生成 | 22427.8 | 27081.6 |

定位阶段少 4215 input，但后续阶段显著增加。不能由此直接说“重复读同一段造成损失”：zg 的可识别重复读取行均值仅 0.2；input 包含多轮历史上下文，后续阶段变贵并不等于新增读取行必然更多。本轮可确定成本发生在哪个阶段，尚未把每一项增加解释成具体的模型内部原因。

## QoderCLI + Qwen3.8-Max：采用工具和后续扩展仍有波动

| 时间块 | baseline input | zg input | baseline calls | zg calls | zg 成功调用次数 |
|---|---:|---:|---:|---:|---:|
| 1 | 239717 | 156760 | 20 | 14 | 1 |
| 2 | 165206 | 264163 | 20 | 21 | 0 |
| 3 | 234493 | 218551 | 19 | 16 | 1 |
| 4 | 248881 | 277621 | 19 | 22 | 1 |
| 5 | 140883 | 172975 | 14 | 15 | 1 |
| 均值 | 205836 | 218014 | 18.4 | 17.6 | 0.8 |
| 中位数 | 234493 | 218551 | 19 | 16 | 1 |

平均 input 增加 5.92%，中位数降低 6.80%；只有两个时间块同时降低 input 和 calls。仅报告中位数会遗漏平均成本增加的事实。input CV 为 baseline 23.91%、zg 24.56%，本轮没有观察到总 input 波动降低。

| 成本阶段 | baseline 平均 input | zg 平均 input | zg − baseline |
|---|---:|---:|---:|
| 首次入口定位 | 19756.2 | 12328.4 | −7427.8 |
| 后续证据扩展 | 158008.2 | 174520.4 | +16512.2 |
| 最终回答生成 | 28071.6 | 31165.2 | +3093.6 |

入口阶段的节省被后续扩展与生成抵消。r02-zg 的工具目录及 MCP 连接正确，但 agent 没有调用 zg；保留该次才能评价“提供 zg 给 agent”的实际效果，不能删掉后只评主动采用工具的样本。

baseline 有两次 Read 错误，分别是读取目录 `/app` 和不存在的 `/app/package.json`；zg 无工具错误。两组确认空搜索分别为 2 / 4 次。工具名正确、执行不报错，是可验证的改进条件，但不能保证 query 合适或总成本更低。Qoder 的温度与 seed 未受验证控制，不能把它描述成 temperature=0 的实验。

## 质量复核：校准通过仍不能排除真实措辞中的漏判

三组中，GLM、Qwen 两位 judge 均通过四条固定校准。GLM 组 10 个答案均获双判 pass；OpenCode + Qwen 原始双判也全部 pass，但 r02-zg 因测量不完整不可报告为 pass；Qoder 组 9 个双判 pass，baseline r04 存在分歧。这些是自动评分的真实结果，不能表述为已验证质量不降。

随后独立的 Codex 源码复核在 OpenCode + GLM 组发现两个明确问题：

- baseline r05 将整个 `_var_dependencies` 表概括为完全来自反汇编 getter 的依赖，遗漏了 `_deps` 在自动分析前保留并合并 static_deps 的路径。
- zg r02 声称 `auto_deps=False` 时不会发生依赖变化触发的自动重算；实际上已提供的 static_deps 仍注册进同一个反向依赖表，参与失效和重算。

两者的主要正常路径解释仍成立，但不能称答案完全无事实错误。源码依据为固定 commit 下 `reflex/vars/base.py:2433` 的静态依赖初始化、`reflex/state.py:783` 的注册及 `reflex/state.py:1998` 的依赖查找。自动 judge 的原始 pass 保留，源码复核的反例另列，不能用两次一致投票消除这些反例。

还发现旧 rubric 的范围问题：required_facts 把 `cache=False` 的直接执行分支写成必须说明，而 OpenCode + GLM 组十个答案都未明确说明，judge 却全部给必要完整性 1。这说明评分器没有严格执行规则，也说明该附带分支是否应当必答需要统一界定。不能事后只对某组放宽标准；本轮不以这份自动评分宣称质量非劣效。

源码复核由 Codex 执行，不是人工独立标注。它能给出可查反例，仍不是自然语言 QA 评分器准确率的总体证明。

以下仅是逐答案的事实复核描述，不是替换原报告的通过率，也不把“未发现矛盾”当作证明正确：

| 组合 | 未发现实质矛盾 | 明确事实问题 | 仍不确定 | 明确问题所在 trial |
|---|---:|---:|---:|---|
| OpenCode + GLM | 7 | 2 | 1 | baseline r05；zg r02 |
| OpenCode + Qwen | 6 | 2 | 2 | baseline r03、r04 |
| Qoder + Qwen | 4 | 4 | 2 | baseline r04；zg r01、r02、r04 |

其他可核查反例包括：

- OpenCode + Qwen baseline r03 把依赖注册称为每个 state 类只做一次；固定源码的动态 add_var 和 reload 路径会再次注册。r04 的“仅实际读取变量变化时重算”排除了缓存时间到期及显式静态依赖等有效分支。
- Qoder baseline r04 称 getter 是依赖分析的唯一输入，遗漏 static_deps；这是 Qwen judge 已识别、GLM judge 漏判的分歧。
- Qoder zg r01 无条件保证分析没有副作用，而追踪器存在 `eval` 路径；r02 称只分析一次，忽略后续编译校验再次 `_deps`；r04 把反向边目标 state 写成依赖变量的定义 state，源码实际记录的是消费该变量的 computed-var 所在 state。

不确定项主要涉及异常发生后是否只剩静态依赖：跟踪器会原地修改共享依赖字典，异常可能保留部分已发现的自动依赖；这是源码路径推断，本轮没有执行异常反例，因此保持 uncertain。

30 个答案均包含主要因果链的轮廓，但这不排除上述错误，尤其 Qoder zg r04 的反向边绑定。旧 rubric 中 uncached 直接调用的必答分支，仅 Qoder zg r04 明确说明，其余 29 个未明确说明。**当前自动 judge 存在规则执行和事实漏判两类缺陷，本轮不能建立等质量收益结论。** 下一版本应把问题必答的原子事实与自行添加的附带断言分开评分，统一修订该条完整性规则，并将这些源码反例纳入新的开发校准；保留独立校验材料，不能把修订后的同题校准成功当作盲测准确率。

逐答案原文引句、源码片段、答案哈希和未覆盖范围见 [GLM 源码审计](readonly-qa-v3-reflex-6-audits/opencode-glm52-source-audit.json)、[OpenCode+Qwen 源码审计](readonly-qa-v3-reflex-6-audits/opencode-qwen38max-source-audit.json)、[Qoder 源码审计](readonly-qa-v3-reflex-6-audits/qoder-qwen38max-source-audit.json)。原始自动分数未被覆盖；审计 JSON 中的建议分项也不作为新的总质量分数。

## 实际环境与可追溯材料

GLM 组 10 次运行全部完成；136 次工具调用全部成功，无工具名或执行错误。八次实际 zg 调用与后端事件逐条一致。

96 个 provider 请求中，86 个主 QA 请求全部为响应标识 GLM-5.2、HTTP 200、temperature=0，且两组各自的工具 schema 哈希稳定。另有每会话一次标题生成，temperature=0.5，其费用在 provider-all 中单独可见，不能说所有模型调用温度都是零。

十次主 QA 的 provider prompt usage、OpenCode native input+cache.read、ATIF final、session 预算计数和报告总量全部一致。标题每次另耗 baseline 607 / zg 617 input tokens；包括标题后五对方向仍不变。output 与 reasoning 分开记录，二者之和与 wire completion 对账。

三组源码、原始索引种子与工作副本全部文档/向量的完整性检查通过；副本存储文件的物理变化单列。GLM job 的索引准备为 10.395 秒，未混入 QA 的 input / calls。

Qoder 十次的原生 message 最终非零用量、原生最终汇总、ATIF steps / final、session 计数和报告一致；没有把补报用量重复累加，也没有再加一次 cache.read。版本、实际模型标识、工具目录、MCP 连接均有记录。原生服务调用没有 OpenCode 同等的 wire 证据，不能宣称做了同等底层请求核验。

随后 [只读审计 CI 34259825953](https://github.com/Cuiyus/zvec-grep/actions/runs/34259825953) 使用 `557a11a48c7599a4a0a2ba9b3e0041722864d4b1`，对三个原 artifact 重新计算指标，均与原报告一致，原文件哈希未变。该任务约 17 秒，不调用模型，不创建新 QA 样本。完整计数审计见 [GLM](readonly-qa-v3-reflex-6-audits/opencode-glm52-accounting-audit.json)、[Qoder](readonly-qa-v3-reflex-6-audits/qoder-qwen38max-accounting-audit.json)，三组机器可读指标及材料来源见 [metrics-summary.json](readonly-qa-v3-reflex-6-audits/metrics-summary.json)。

| 组合 | 原 artifact ID | 原始文件数 | 本地核验边界 |
|---|---|---:|---|
| OpenCode + GLM | 10068365935 | 365 | 完整 ZIP SHA、解压原文件及原始事件 |
| OpenCode + Qwen | 10068667396 | 366 | 只读 CI 重新计数、派生 JSON、原文件哈希记录；未在本地验证完整 ZIP |
| Qoder + Qwen | 10068764399 | 153 | 完整 ZIP SHA、解压原文件及原始事件 |

GitHub 记录的 ZIP SHA-256：

- GLM：`85beae877a7bd25f56e863e28db7e38d86410dcd058a6591d8e16022b4783b57`。
- OpenCode + Qwen：`af7d205ab5657ba3a08c910a01eb44dab4f97640fa9c9ef17dedbdec712de742`。
- Qoder：`e2d59903bef8b711e4e9455c647d4cb4b6af852106b376b0ab96dae3df69c1b2`。

OpenCode + Qwen 的下载域名在本地网络不可用，因此由 GitHub 上的只读审计读取原 artifact，提取可读记录。不能把派生 JSON 称为完整归档。本地实验材料位于 `benchmarks/swe-qa-bench/runs/readonly-ci/34255587426/`；本目录提交的审计记录是后来生成的派生材料，各自哈希及来源见 [manifest.json](readonly-qa-v3-reflex-6-audits/manifest.json)。

## 本轮落实了什么，仍不能推出什么

1. **检索结果可解释**：能够分别指出函数入口命中、排名与返回长度，以及重复执行和跨环境输出差异；不再用“搜索一次覆盖九段完整证据”作为成败标准。
2. **工具及环境控制有实际证据**：固定题目、源码、zg/agent 版本与只读工具；OpenCode 对实际 temperature、模型身份和工具 schema 做验证，Qoder 明确保留不可控项。
3. **成本结论可复算**：30 次原始会话全部保留，input 口径对账，预算/网络失败不删除，五个时间块及阶段成本均公开。GLM 减少猜词探索的机制有轨迹支持；Qwen 两种集成没有观察到平均 input 节省。
4. **质量门槛有可查反例**：双 judge 不能替代源码事实核验。本轮严格保留“质量非劣效未证实”，不会因原始 pass 或成本下降发布等质量收益声明。
5. **同题开发的边界明确**：五次重复用于观察本 case 的波动，不证明跨题泛化；没有隐藏重跑，也没有为了正收益继续加测。后续修改检索输出、调用策略或 rubric 时，需重新冻结协议，保留本轮作为基线。
