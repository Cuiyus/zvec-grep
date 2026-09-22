# Workspace-Bench Lite CN 原始任务 334 / 363：Qoder + zg 0.2.2 smoke

本报告记录原始任务的单次 smoke，不是 Workspace-Bench 官方 ClaudeCode judge 或排行榜成绩。两组固定使用 Qoder CLI 1.1.45、Qwen3.8-Max、reasoning high、相同的内置工具与输入；仅 with-zg 组通过 `zg install --target qoder --yes` 增加 zg 0.2.2 原生 MCP，远端 embedding 为 `qwen/qwen3.7-text-embedding`。用户任务原文、输出文件名和 rubric 均未改写。自定义 GLM-5.2 judge 使用原始 rubric。

| 任务 / run | baseline | with-zg | 可作出的结论 |
| --- | --- | --- | --- |
| [334](https://github.com/Cuiyus/zvec-grep/actions/runs/35711197626) | 完成；自定义 rubric 16/18；输入 2,197,976 tokens；159 次工具调用；947.37 秒 | 完成；17/18；输入 1,509,259 tokens；172 次工具调用；996.60 秒；正式 zg 调用 0 | 形成有效单对 smoke，但 zg 从未参与任务，差异不能归因于 zg。 |
| [363](https://github.com/Cuiyus/zvec-grep/actions/runs/35731286815) | 3600 秒墙钟限额耗尽，未产生最终消息；输出目录留有新的、可解析的 54 页 PDF；观察到的输入 token 下界 8,127,741；113 次工具调用；未进入自定义 rubric judge | 2869.27 秒完成；31 页 PDF；自定义 rubric 22/22；输入 8,900,892 tokens；129 次工具调用；正式 zg 调用 0 | 原 harness 的完成率为 1/2，不能计算有效的配对质量或 token 收益。with-zg 的评分不能当作 zg 收益。 |

Task 363 的 with-zg 安装清单有效，Qoder 启动与 MCP 连接检查通过；独立预检成功执行了原生向量检索。该预检不属于正式任务调用或 token 指标。Task 334 与 Task 363 的正式任务均未调用 zg。

Task 363 的 baseline 在限额前写入 PDF，随后继续检查并修复目录超链接，最终因墙钟限额退出。当前 harness 要求 Qoder 会话成功结束才将样本记为 `completed`，因此原始 CI 没有对这份 PDF 打分。[上游固定版本的 runner](https://github.com/OpenDataBox/Workspace-Bench/blob/3fbd0f1a136720fece86786545983e26642c3db2/evaluation/src/agent_runner.py#L1223-L1276) 则会收集超时后仍存在的输出文件，并在找到输出路径时把任务执行状态记为 `passed`，同时保留 `runnerStatus: timeout` 和 `partialOutputCollected: true`。这说明我们的完成判定比上游更严格；**不能因此把原始 baseline 的 rubric 分数写成 0，也不能把超时伪装成正常会话完成**。

为核查截止时的实际交付质量，[独立诊断 CI](https://github.com/Cuiyus/zvec-grep/actions/runs/35749201988) 只重评了已保留的 baseline PDF，没有重新运行 Qoder。它从固定数据集 revision 下载原始四篇 PDF 和 metadata 并核验 SHA；以相同的 PDF 提取引擎重建了原 judge 来源文本，复现了 with-zg 原评分的完整 prompt SHA-256，然后对 baseline 文件应用相同的自定义 GLM-5.2 原始 rubric 评分。结果为 **22/22**；with-zg 原评分也是 **22/22**。诊断产物明确保留 `trial_completed: false`、`baseline_execution_status: budget_exhausted`、3600 秒墙钟上限以及 **8,127,741 个输入 token 的观察下界**。这是文件在截止时的诊断分数，并非原 CI 的正常完成配对、官方 ClaudeCode judge 或正式的 token 节省率。两份 PDF 内容差异较大，但此自定义 rubric 已满分，无法以本次评分分辨质量收益。

前一次 [Task 363 run](https://github.com/Cuiyus/zvec-grep/actions/runs/35723608351) 的 baseline 曾在 1891 秒完成并生成 21 页 PDF，却因 Qoder `modelUsage` 中零 token、零费用的 `lite` 空桶被旧 parser 误判为模型回退；该误判已在 `e5b7ecd` 修复。它与本次 with-zg 样本来自不同 CI run，不能直接拼成原定的同批配对结果。

Task 363 的主要工作是阅读四篇给定论文并排版长篇 PDF；已观察到的模型时间主要花在报告和超链接制作上。两次正式 with-zg 样本都没有调用 zg。继续重复相同任务五次可以估计 Qoder 的输出波动，却无法测量 zg 检索收益；在确认自然检索调用路径前，不应把重复样本的质量差异解释为 zg 效果。
