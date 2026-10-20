# 原生 Claude q001 确认：c02 证据归档

本目录保留 c02 按预定 zg → baseline 顺序完成的一对执行。执行、计量与来源审计已完成；**四份答案的盲评和整体确认结论由阶段级报告另行汇总，本目录不作质量评分或整体判定。** 原始答案和日志按字节保留，本次归档未读取匿名映射或评分。

[执行复核](review/execution/c02-execution-review.json)及其[脚本](review/execution/review-c02.py)、[全部 60 次实际工具调用的来源审查](review/scope/report.json)均完整保留。来源审查包含唯一一次 `/workspace` zg 调用的全部 36 个结果与逐行核对：291 行中 280 行精确一致，11 行省略首行缩进，无非空白差异。原生结果全文、每次调用的输入/结果和位置哈希保留在 `review/scope/`。

[环境导航记录](review/scope/zvec-grep/environment-navigation-observations.json)明确保留 `ls /`、`ls ~` 的目录名观察及后续 evidence 命名路径的无内容探测，不宣称所有访问都位于 `/workspace`，也不把没有返回内容的探测称为读取 gold。[本地 grep 报错](review/scope/zvec-grep/local-grep-diagnostic.json)保留为诊断，不当作成功检索。c01 审阅期望修正与复用方法文件仍在既有 c01 归档，通过 retention 精确映射引用，不重写原报告。

证据来自 [fork run 39343873279](https://github.com/Cuiyus/zvec-grep/actions/runs/39343873279)，attempt 1，head `fd0c94c37c95e2afb4b5482530904574f5719c5f`；c02 artifact ID `12232194919`。原始 ZIP 为 43,183,263 bytes，SHA256 `9ec61198c41813d6542a16ad7091bd824e7edfd608a92ee7cac26c71e3b71317`。130 个 raw 文件中仅省略重复 `raw/zg-install.tgz`（123,165,898 bytes，SHA256 `fbe4bd201c8a0520f2373f1039e1d0805a38781c6e2f3ce11b83310d23dc6522`）；原始 ZIP 和安装包继续保留在本地原归档及 fork artifact，不重复加入 Git。

[retention.json](retention.json)记录全部原路径→归档路径、复制字节验证、现有 c01 依赖的精确映射、遗漏文件身份及获取方式。[archive-manifest.json](archive-manifest.json)覆盖本目录除清单自身外的所有实际文件。原 metadata、日志与脚本内绝对路径保持原样。CI 上传前实际 secret 值扫描已成功，本地补充模式检查见 [secret-scan.json](secret-scan.json)。

本归档不合并开发结果、不改变冻结门槛，不授权额外试验、替换或重试。
