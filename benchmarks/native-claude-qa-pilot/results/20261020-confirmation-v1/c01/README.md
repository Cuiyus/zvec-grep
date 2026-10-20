# 原生 Claude q001 确认：c01 临时归档

本目录仅保留 c01 已完成的一对 baseline → zg 执行及其独立执行、计量和来源审计。**质量评分待完成，c02 尚未完成，因此没有整体确认结论。** 原始答案和日志按字节保留，未在本次归档中评分或读取匿名映射。

[中间摘要](interim-summary.json)、[执行复核](review/execution/c01-execution-review.json)和[全部 86 次工具调用的来源审查](review/scope/report.json)分别保留。执行审阅的 initial、shape-expectation 结果及[期望修正说明](review/execution/c01-auditor-expectation-correction.json)未覆盖或删除。来源审查保留两次 MCP 的完整结果、逐行比对及[非空白字符差异](review/scope/zvec-grep/non-whitespace-snippet-difference.json)：原生 snippet 在 `local_wal_file.h:34` 返回 `}`，冻结源码为 `};`；不将其算作逐字匹配，也不推断内部原因。

证据来自 [fork run 39343873279](https://github.com/Cuiyus/zvec-grep/actions/runs/39343873279)，attempt 1，head `fd0c94c37c95e2afb4b5482530904574f5719c5f`；c01 artifact ID `12231279241`。原始 ZIP SHA256 为 `0a8272626ba3053fd00a5f489c27bf94998e7cfeaffc0da8667acca3155b89e2`。130 个 raw 文件中仅省略重复的 `raw/zg-install.tgz`（123,165,898 bytes，SHA256 `fbe4bd201c8a0520f2373f1039e1d0805a38781c6e2f3ce11b83310d23dc6522`）；原始 ZIP 和安装包仍保留在原本地归档及 fork artifact，不重复加入 Git。

[retention.json](retention.json)记录原路径→本目录相对路径、全部复制文件的字节校验、遗漏文件身份和获取方式；[archive-manifest.json](archive-manifest.json)覆盖本目录除清单自身以外的全部实际文件。原 metadata、日志和脚本内的绝对路径保持原样。CI 在上传前已完成实际 secret 值扫描，本地补充检查见 [secret-scan.json](secret-scan.json)。

本目录是 c01 interim 证据快照，不合并开发结果，不替代原冻结门槛，也不授权额外运行、替换或重试。
