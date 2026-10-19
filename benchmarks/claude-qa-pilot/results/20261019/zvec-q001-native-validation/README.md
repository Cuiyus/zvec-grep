# zvec q001 原生辅助验证

这是现成 **post-fix 包** `@zvec/zvec 0.7.0` / `@zvec/bindings-darwin-arm64 0.7.0` 的辅助运行证据，**不是冻结基线 `ec8a78e` 的故障复现，也不是 exact frozen source 的验证**。未安装或构建依赖，未运行付费模型。

- [有效辅助运行](native-smoke-070-4j6a26pv/results.json)：150 条文档经 SIGKILL、恢复、optimize、独立新进程重开后全部保留；无崩溃对照通过。ID/标量精确一致，向量使用统一绝对容差 `1e-6`，最大观测误差 `1.49e-8`。没有验证查询排名。
- [首次 FP32 夹具失败](native-smoke-070-pbqbq7yx/results.json)：逐位比较向量在预定 SIGKILL 前因 FP32 舍入误差失败，**不是数据丢失复现结果，不计为有效回归或独立重复**。原始 [stderr](native-smoke-070-pbqbq7yx/1-crash-create.stderr)、[夹具](native-smoke-070-pbqbq7yx/smoke.cjs) 和其他记录完整保留。
- [版本与二进制元数据](native-smoke-070-4j6a26pv/metadata.json)：npm 发布 `gitHead` 经仓库子模块声明追溯到包含 #600 的 core commit `8321c1314a…`；**发布 gitHead 和子模块声明不是已安装二进制的构建证明**，不能据此把结果归给冻结提交。
- [原始报告](native-smoke-070-4j6a26pv/validation.md)、[源码审计](native-smoke-070-4j6a26pv/source-audit.json) 和独立补丁记录了 #579 的 35 行插入差异，以及后续仅验证 #600 的隔离构建方案。共享题目、源码和 rubric 未改动。

两个运行目录中的所有文件均逐字节复制；日志、夹具、原始报告及元数据里的运行时绝对路径保留原样，不作为归档导航链接。本目录仅收录文本证据，没有数据库或 node_modules。归档导航均使用相对路径。

[证据清单](evidence-manifest.json) 记录原始文件大小、SHA-256 和复制校验；[SHA256SUMS](SHA256SUMS) 覆盖全部文本文件（不含清单自身 SHA256SUMS）。可在本目录运行 `shasum -a 256 -c SHA256SUMS` 校验。夹具保留当时运行环境的路径，不会在归档时重新执行。
