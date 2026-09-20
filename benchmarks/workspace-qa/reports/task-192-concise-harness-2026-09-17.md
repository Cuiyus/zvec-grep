# Task 192：concise-report-v1 配置审计

执行结果已归档：[首个完成配对与评分审计](task-192-pdf-concise-result-2026-09-17.md)。
下文保留启动前的配置说明，不替代实际结果。

2026-09-20 补充：[短诊断](qoder-zg-routing-diagnostic-2026-09-20.md) 在同版 Qoder 中发现
CLI 请求的 2,048 输出上限没有反映在观察到的客户端请求中，六个请求均为 32,000。
因此下文的 16,384 仅表示历史 CLI 传参，不能视作已生效上限；历史 Task 192 请求未被观测。
`kCr` 是否抑制升级也取决于值是否真正传入主 agent loop，不能仅凭 CLI 注册行为确定。

这是针对 [上一轮未完成原因](task-192-pdf-smoke-2026-09-17.md) 的新实验条件，
不是成功结果报告，也不声称已经修复上游 Qoder 的压缩实现。
原始 task、全部 17 条 rubric、完整角色工作区、PDF 转换方式、安装方式和模型版本均保留。

## 已核实的原生行为

本地 `qodercli --version` 为 1.1.45，`--help` 明确列出 `--max-output-tokens`。
同时审计了 `@qoder-ai/qodercli@1.1.45` 发布包中的 `bundle/qodercli.js`，
SHA-256 为 `86565469a3a0dd2dcede554c6056678cb7423f12e1929b4b23b9d6c340559418`。

- CLI 将该参数注册为运行时模型配置中的 `generateContentConfig.maxOutputTokens`。
- `kCr` 的输出预算升级分支在显式指定上限时不执行；但 `yCr` 仍可以在 `max_tokens` 停止原因后追加自动续写消息，恢复次数上限为 2。
- `compacting` 状态由原生压缩进度事件触发，压缩完成或失败后再清空。

因此，单独降低输出上限不保证完成任务，也不等于限制整个会话的总输出量。
实际 provider 请求没有独立 wire tap，manifest 明确记录 `max_output_tokens_wire_verified=false`；
后续需核对原生 usage、超限通知、续写与终态，不能仅凭传参声称后端严格执行了上限。
官方文档对该参数的定义见 [CLI 参数](https://docs.qoder.com/cli/cli-reference)，
压缩机制见 [Context compaction](https://docs.qoder.com/qoder/context-compaction)。

## 新条件与公平性

两组共用 `concise-report-v1` 公共交付说明：

- 以一条最终回答交付完整报告，目标为 2,500～4,500 个中文字符；完整覆盖原题优先于这个风格目标，不在 harness 中裁剪答案。
- 使用紧凑的关键数据表、简短原因分析和来源引用，避免大段引用、反复重述及庞大附录。
- 证据收集围绕原题展开，避免无范围的大段读取和重复读取；已有证据足以回答时完成报告。

说明不包含公司名称、具体文件位置、答案、rubric 内容或强制工具选择。
两组唯一的工具接入差异仍是标准 zg 安装/MCP；说明正文完全相同。
原题在实际指令中原样保留，公共说明是明确披露的 harness 附加条件，不能把整体提示称作“完全未变”。

公共说明 SHA-256：`8945d3c09fb6848f538500b11b7759f9c9731720aafa87c3a13f00286451b194`。
锁文件校验正文哈希，manifest 记录策略版本、正文哈希和输出参数；旧结果不混入新条件，
显式输出控制也不能通过通用 continuation 接口沿用旧 trial。
其他实验默认仍使用原说明，不添加输出参数。

| 条件 | Baseline | With-zg |
|---|---|---|
| task / 重复数 | 原始 192 / 1 | 原始 192 / 1 |
| Agent | Qoder 1.1.45 / Qwen3.8-Max | 相同 |
| 公共交付说明 | concise-report-v1 | 相同 |
| 原生单次输出参数 | `--max-output-tokens 16384` | 相同 |
| 累计输入 / 时间上限 | 6,000,000 / 1,800 秒 | 相同 |
| 模型请求 / 工具调用上限 | 60 / 120 | 相同 |
| 原生请求重试上限 | 2 | 相同 |
| zg | 不接入 | 0.2.2，`zg install --target qoder --yes` |
| Embedding | 无检索索引 | 远程 qwen/qwen3.7-text-embedding |

不关闭原生压缩，不扩大上下文窗口，不调整模型、temperature 或模型 seed。
这是公共交付说明和单次输出控制的组合实验，不能分别估计两项改动各自的因果效果。
是否完成、是否自然调用 zg、评分和 token 差异均等待实际运行；低分或零调用不触发重抽样。

## 状态可见性

`qa-session.py` 在收到原生 stdout 事件时记录单调时间偏移和接收时间，
额外输出 `native-event-timing.jsonl` 与原子更新的 `native-progress.json`。
仅记录事件类型、时间、压缩状态和累计已完成压缩区间，不复制提示、工具参数、答案或模型隐藏推理。
诊断写入失败不会终止会话，失败计数单独保存。

外层 30 秒 heartbeat 显示最后事件距今时间及当前压缩持续时间。
它反映客户端事件阶段，不等于已测得压缩内部网络、排队或模型计算的独立耗时。
这些记录不改变原生事件、工具参数、token 聚合、预算停止规则或原始回答。

工作流仍只启动一对 smoke，artifact 前缀为 `workspace-qa-task192-pdf-text-v1-concise-v1`。
所有此前失败记录继续保留在原报告和原始 CI artifact 中。
