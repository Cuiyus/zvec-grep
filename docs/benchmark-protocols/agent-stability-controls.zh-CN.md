# 三组只读 Agent 实验的稳定性控制

核查日期：2026-09-10。范围：OpenCode 1.18.4 + GLM-5.2 / Qwen3.8-Max，以及 QoderCLI 1.1.45 + Qwen3.8-Max。依据包括官方文档、固定版本源码及本工作树实现；本说明未运行模型或 CI，也未修改执行配置。检查时工作树基准 commit 为 `50e1a028bc3f607fcdb1113c4a66461f1e467c86`。

**建议保留当前受控配置，先把实际请求、工具响应和停止原因完整记录。低温、固定版本和工具白名单可以减少可控变化，但不能保证完整轨迹确定；重复实验、配对和置信区间解决的是效果估计的不确定性。** 不应为了得到相同轨迹而强制使用 zg、提供查询词或重跑到满意为止。

## 1. 当前实际支持到哪里

当前配置入口为 [readonly_agents.py](../../benchmarks/swe-qa-bench/zg_bench/swe_qa/readonly_agents.py)，请求与预算观测在 [qa-session.py](../../benchmarks/swe-qa-bench/scripts/qa-session.py)。OpenCode 实际接入阿里云 MaaS 的自定义兼容端点；Qoder 使用原生 PAT 账号路由。Z.AI 原生 API、阿里云公共 API 与这两个具体服务路径不能直接视为同一参数契约。

| 控制项 | 已核实事实 | 本轮采用与限制 |
|---|---|---|
| OpenCode temperature | 固定版本先检查模型 `temperature` 能力，再取 agent 温度；自定义模型默认能力为 false。[请求构造](https://github.com/anomalyco/opencode/blob/v1.18.4/packages/opencode/src/session/llm/request.ts)、[模型构造](https://github.com/anomalyco/opencode/blob/v1.18.4/packages/opencode/src/provider/provider.ts) | 已同时设置 `provider.models.<id>.temperature=true` 和 `agent.build.temperature=0`。以实际 wire 的零值判生效，不以配置文件推断。 |
| OpenCode top_p | 官方 agent schema 支持 `top_p`；1.18.4 对 Qwen 自动给出 `topP=1`，GLM-5.2 无此默认值。[schema](https://github.com/anomalyco/opencode/blob/v1.18.4/packages/core/src/v1/config/agent.ts)、[默认参数](https://github.com/anomalyco/opencode/blob/v1.18.4/packages/opencode/src/provider/transform.ts) | 当前不额外调参：Qwen 请求有 `top_p`，GLM 请求省略。不要写成两者采样参数完全相同，也不要再把 top_p 降低当作确定性保证。 |
| Qoder temperature / top_p | 官方主会话参考页没有直接采样 flag；[Subagent](https://docs.qoder.com/cli/subagent) 的 temperature 不能直接证明主会话配置。1.1.45 发布二进制静态检查存在 `model.preferences[modelId].temperature/topP/topK` 到 generation 的路径。 | 当前 runner 没有设置该路径，原生账号服务是否采用也未验证，故记为 **未控制 / unknown**。不能将现有 `unsupported_by_verified_qoder_1.1.45_cli` 文案理解为整个 CLI 完全不支持。 |
| seed | 阿里云文档描述 seed 为尽可能复现；当前 runner 没有传模型 seed。Qoder 主 generation 的可控 seed 未核实。[阿里云参数](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions) | `order_seed=1729` 只固定 AB/BA 顺序。OpenCode SDK 存在额外 provider 参数透传，但未经当前版本 wire 和具体服务验证的 seed 不列为已控制。 |
| fingerprint / 模型快照 | 阿里云公共兼容接口文档将 `system_fingerprint` 标为固定 null；Qoder 的服务模型目录可更新。[阿里云响应字段](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions)、[Qoder model](https://docs.qoder.com/cli/model) | 保留响应 model、请求 ID、时间、端点、非空 fingerprint（若有）。响应别名相同和 fingerprint 缺失都不能证明权重版本固定；不承诺逐字复现。 |
| 最大轮次 | OpenCode `steps` 达到上限后要求模型用文字结束；Qoder 有 `--max-turns`。[OpenCode agents](https://opencode.ai/docs/agents/)、[Qoder CLI](https://docs.qoder.com/cli/cli-reference) | 已设 30；仍配合外部 60 次工具、300,000 input、900 秒预算。30 轮不等于含重试、标题、压缩在内至多 30 个 HTTP 请求；在途操作可能超阈值。 |
| 重试 | OpenCode SDK 调用默认 `maxRetries=0`，但会话外层仍运行 `SessionRetry.policy`。[LLM 层](https://github.com/anomalyco/opencode/blob/v1.18.4/packages/opencode/src/session/llm.ts)、[会话层](https://github.com/anomalyco/opencode/blob/v1.18.4/packages/opencode/src/session/processor.ts) | Qoder 已设 `--max-model-request-retries 0`；透明转发器不加重试。不能说所有 agent/辅助请求/服务端重试均关闭。保留失败请求、原生重试与未知费用，不将最终有答案等同完整成功。 |
| 并行工具 | 阿里云公共 API 有 `parallel_tool_calls`；当前 runner 未显式设置，Qoder 没有已核实的全局工具串行开关。 | 保留原生策略并记录同一模型轮中的全部调用与返回顺序。不靠改变并行默认求相同轨迹；禁用子 agent 不等于禁用并行工具。 |

Qoder 静态核查对象是本地已安装的 `qodercli-1.1.45`，SHA-256 为 `6f652994a6723b2f1d1bc5bcf09c5c47083db8c1d49331b3f05373afc094b654`；这是发布二进制代码路径证据，不是服务端采样生效证据。主会话参数、历史保留与上下文设置应分别参考 [CLI reference](https://docs.qoder.com/cli/cli-reference)、[settings reference](https://docs.qoder.com/cli/settings-reference) 和 [release notes](https://docs.qoder.com/release-notes/qoder-cli)，滚动文档不能替代固定版本验证。

## 2. 可以继续直接采用的控制

- **冻结运行边界。** 固定 CLI、zg、embedding、源代码 commit、索引内容、同一容器路径和每组 provider route；每次新容器、新会话与独立配置。禁止自动更新、外部 skills、非实验 MCP、子 agent 和写操作。OpenCode 配置会合并，单独设置 `OPENCODE_CONFIG` 不等于清空其他配置，因此继续使用干净容器并禁用项目配置。[OpenCode 配置优先级](https://opencode.ai/docs/config/)
- **冻结工具契约。** baseline 保留原生 read/grep/glob；zg 组只增加真实注册的搜索工具。逐轮保存工具名称、完整 schema 的有序哈希及工具输出哈希；不同 profile 目录本就不同，应在各自 profile 内比较。两组使用同等的工具名称提示，不提供正确工具调用、查询词或答案线索。
- **保留当前温度路径。** OpenCode 的最小有效组合是模型能力 `temperature: true` 加 `agent.build: {"temperature": 0, "steps": 30}`；Qoder 保持温度 unknown。现有离线请求契约测试使用真实 1.18.4 和本地假服务，能够验证发包；它不能验证远程模型执行了何种采样。
- **保留原生修复并显式区分。** 1.18.4 内置工具名大小写修复和 invalid tool 处理；因此“旁路不修复”不等于“agent 不修复”。应同时记录 provider 原始 tool call 与 agent 执行事件，不隐藏模型猜错名称或恢复成本。[固定版本工具修复](https://github.com/anomalyco/opencode/blob/v1.18.4/packages/opencode/src/session/llm.ts)

## 3. 上下文、输出和思考的剩余控制边界

当前 runner 没有显式冻结模型上下文声明、压缩阈值、每轮输出上限或思考强度。OpenCode 支持 `compaction.auto/prune/reserved`，源码按模型 output limit 和运行时上限计算每轮最大输出；Qoder 自动压缩及 `model.maxSessionTurns` 不能等同于执行轮次限制。[OpenCode compaction](https://opencode.ai/docs/config/#compaction)、[输出上限源码](https://github.com/anomalyco/opencode/blob/v1.18.4/packages/opencode/src/provider/transform.ts)、[Qoder 上下文说明](https://docs.qoder.com/cli/troubleshoot-performance)

本轮先保留相同版本的现有策略，记录实际 `max_tokens`、压缩事件、截断、终止原因与可见返回长度。不要为了降低 token 数截掉 baseline 的证据或给 zg 更大的输出预算；也不要在看到结果后关闭压缩。若另开“固定上下文”诊断，需先确定预算能容纳任务，独立登记两组完全相同的规则，保留溢出/截断失败，不能与原协议混报。

`--thinking` 输出选项不能证明服务思考强度已固定。阿里云 Qwen3.8 默认保留历史 thinking，并要求历史 `reasoning_content` 在原字段正确回传、计入 input；Z.AI 的 `thinking.clear_thinking` 则属于另一服务契约。优先审计多轮 wire 中的真实字段与回传内容，当前不切换思考模式来追求一致。[阿里云思考参数](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions)、[Z.AI 原生接口](https://docs.z.ai/api-reference/llm/chat-completion)

## 4. 如何验证“更稳定”，以及怎样估计收益

本轮对既有 v3 产物的只读核验发现：所检查的两种 OpenCode 组合，各五次起始主 QA 请求在组合内部的字节哈希一致，temperature 均为 0；Qwen 存在 top_p，GLM 省略。本轮将此记录于 [v4 分析](../benchmark-results/readonly-qa-v4-reflex-6.zh-CN.md) 的 `initial_request_identity` / `initial_request_body_consistency`。这排除了这些起始请求在客户端字节层面的漂移，仍未固定后端权重；起始相同也不保证后续工具参数、证据与轨迹相同。[v3 原始结果](../benchmark-results/readonly-qa-v3-reflex-6-2026-09-09.zh-CN.md) 已展示各轮成本分散，不能因 temperature=0 称为确定性流程。

采用以下分层检查，避免把配置、行为与统计混为一谈：

1. **配置是否生效：** 离线假服务验证每个实际请求的 model、temperature、top_p、工具目录及 schema；候选 seed/并行/输出参数也必须先过发包检查。远程服务是否支持仍需单独契约依据，成功返回不自动证明未知参数被采用。
2. **变化从哪里开始：** 比较同 profile 的首个请求字节、首次原始工具调用、参数、工具返回哈希和后续消息序列；记录第一处分叉。完整请求中路径、时间、工具目录或维护消息不同时，先解释客户端输入差异。工具参数格式合法不等于搜索参数语义正确，schema 哈希一致也不是模型正确调用的证明。
3. **行为方差是否降低：** 对同题同组的全部预定运行报告成功/失败、路由与查询差异、首次证据成本、input/tool calls 的原始值和 SD；CV 要结合均值解释。强制 zg、固定答案或回放工具响应改变了干预，只适合另标诊断实验。
4. **收益估计是否足够确定：** 独立重复、时间块内随机 AB/BA、冻结样本和适当区间，降低估计误差与顺序混杂；它们不会让单次 agent 本身更确定。五次同题仍是一道题，扩大外推范围需要更多独立任务。先满足预设质量门槛，再报告全部计划运行的成本差异、失败与缺失；不能只选两边都答对或实际用了 zg 的运行。

降低温度也可能改变质量和搜索策略，不能预设它一定更准确。当前能证明的是“某些可控输入已冻结、某些差异已可观测”；是否降低行为方差、是否在质量门槛下稳定省成本，仍由预先登记的重复结果决定。

## 5. 后续可以单独验证的稳定化方案

下面是建议的实现方向，并非本次已经改变或实测有效的配置。应一次改变一类因素，冻结新的实验编号，并给 baseline / zg 相同的通用执行规则；不能与原生 agent 的 v3 结果混为同一实验。

| 方案 | 具体控制 | 能减少什么变化与代价 |
|---|---|---|
| 确定的工具执行器 | 固定文件枚举与结果展示顺序、稳定截断位置；模型同轮给出多调用时，按确定顺序执行并返回；保留所有原始调用与失败 | 减少工具反馈与并发时序的变化。串行执行可能增加耗时，也不保证模型下一轮选择相同 |
| 更明确的参数契约 | 对类型、范围、互斥参数做程序校验；支持时用受约束的工具参数生成；错误响应采用固定结构和固定恢复次数 | 减少无效调用和不一致的恢复路径。合法字符串依然可能是差的检索表达，例如当前数组样式字符串不能仅凭类型判错；服务是否支持约束生成要另行核实 |
| 程序控制阶段与预算 | 固定“定位入口、读取证据、回答”的阶段边界及预算；根据通用证据要求决定是否继续，避免让模型无限扩展阅读；不向 agent 提供本题 gold 锚点 | 减少调度、继续探索与停止决策的自由度。会形成一种新的受控 agent，可能过早停止或损失质量，需要单独验证 |
| 固定中间状态的分叉诊断 | 选定已记录的某一轮输入或同一组工具反馈，独立重复后续决策；比较第一次实际行动分叉，而不是仅比较随机 call ID | 有助于区分初始改写、后续决策和工具反馈的影响。属于反事实诊断，回放反馈的耗费不能当作真实 E2E 成本 |

当前最有依据的顺序是：保持已验证的低温配置，先控制客户端输入和工具执行；若后续自由探索仍造成大幅波动，再测试程序化阶段控制。是否更稳定，应看同等质量约束下的行动分布、失败率和成本离散度，不能只看 query 字符串是否变少。
