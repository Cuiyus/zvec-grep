# Retrieval-only：SWE-QA 20 题手动 CI

更新日期：2026-09-20。运行说明见 [benchmark README](../benchmarks/zg-retrieval/README.md)，统一工作流见 [Retrieval-only](../.github/workflows/retrieval-only.yml)。

## 执行入口

只接受 `workflow_dispatch` 手动触发，不接受 push、pull_request 或定时触发。发起者及本次重新运行者均须具有仓库 maintain 或 admin 权限；每个 job 都重新核验，避免单独重跑失败 job 时沿用旧授权。权限读取失败则停止，write/triage/read 角色不能执行本测试。

默认只运行 ZG；输入 `run_semble=true` 后在同一个工作流增加 Semble。`modes` 默认 hybrid，也可以选择 hybrid,fts,vector。每个 ZG 模式均保留 short/full 两组。

唯一正常结果入口为 **Retrieval results** job 的 Summary。表中直接并列所有启用组，未启用 Semble 明确显示“未启用”和 —。分片不再各自发布长摘要。缺失、无效、调用失败及完整成功分开标识；无效实验不能用零分伪装成完成。

## 数据与检索协议

题目来自 [Actions 35206585943](https://github.com/Cuiyus/zvec-grep/actions/runs/35206585943)，完整原题、UTF-8 hash、11 个源码提交与来源指纹保存在 [source.lock.json](../benchmarks/zg-retrieval/data/source.lock.json)。20 道原题每类 what/where/how/why 各 5 题；不重写 query、不加子 query、不运行回答 Agent 或 LLM Judge。

每次在 corpus 外安装待测 npm tarball，通过官方安装生成的 stdio MCP 配置调用产品。每个仓库新建索引，只缓存模型下载；检索前后校验源码、模型和索引身份。题目、标签和报告不进入检索 corpus。

ZG 主测试使用原始 query、Top-10、autoUpdate=false、freshness=eventual。在同一索引和 MCP 会话内，每题 short 连续 5 次，再 full 连续 5 次。每组仅第 5 次作为质量观测，仍是 20 个质量样本。默认 hybrid 共 200 次调用；全三模式共 600 次。full 返回命中检索单元已有的完整内容及 outline，不额外读取整文件。

可选 Semble 固定源码 `0051e000fcaac69a9c5d081ebbc8d4cb8508160b`、固定模型 revision 与 Python 依赖。原生 MCP 使用 top_k=10、content=code、max_snippet_lines=null，保留默认混合检索与规则重排。共 100 次 MCP 调用，另以同一持久化索引执行 20 次 `index.search()`，逐项校验第 5 次 MCP 的路径、范围、排名、分数和完整内容。SDK 回放不进入质量样本或 MCP 延迟。

## 结果表

| 指标 | 定义与分母 |
| --- | --- |
| 文件 Hit@1 | 20 题中，第 1 条结果命中任一已标注相关文件的比例 |
| 文件 Hit@5 | 原生前 5 条至少命中一个标注文件的比例 |
| 文件 Hit@10 | 原生前 10 条至少命中一个标注文件的比例 |
| 文件 MRR@10 | 首次命中排名 r 的 1/r，未命中为零；全部 20 题等权平均 |
| Semble nDCG@10 | 完整移植固定版官方首次目标排名、二元收益与目标数 IDCG；按 11 个仓库宏平均 |
| 平均输出（KiB） | 每题第 5 次成功响应公开文本的 UTF-8 字节均值 / 1024，不是模型 token 数 |
| 延迟 P50（ms） | 所有有效成功 MCP 搜索调用耗时的中位数；不含建索引与 SDK 回放 |

输出和延迟分别显示有效样本数，完整成功时通常为每组 20 个输出样本、100 个延迟样本。产品失败调用不进入这两类操作测量，但失败题在质量分母中仍记零。固定 short-before-full 顺序与不同机器/引擎环境会影响延迟，因此这些观测不构成受控速度优劣结论。

严格源码锚点 Hit/RR/MRR、互补组 nDCG、nDCG@5、独立文件存在率及分类评分已从当前评分器或报告中删除。公开 rank、逐题 RR、目标排名及配对一致性作为计算/完整性证据保留，不再生成额外评分表。原始执行计时和索引记录保留用于审计，不混入主结果表。

## 标签及匹配

五项质量指标共用 [39 个冻结文件目标](../benchmarks/zg-retrieval/gold/semble-file-v1.json)。文件目标从原始 accepted 路径去重投影，bridge-only 文件不计分。两个 AI agents 基于固定源码提议并交叉检查的旧标注作为来源记录保留；声明锚点不再参与检索命中判定。

匹配采用 Semble 的路径规则，保留原生返回位置；重复文件不折叠、不重新编号。片段长度、outline 和是否完整显示函数声明不会改变这五项质量指标。Semble nDCG 的 JavaScript 移植由固定上游 Python 原始函数作差分 oracle 验证。

这些是公开开发题与部分正例，尚未经独立人工盲审。命中文件不等于返回足够回答证据，也不是完整召回率；20 题中的一个 Hit 变化就是 5 个百分点。标签复审见 [指标有效性记录](./zg-retrieval-metric-review.zh-CN.md)。

## 完整性和失败处理

每个 task/mode/preview/repetition 必须有独立原始响应与 hash。离线聚合重新解析公开结果，并核对请求参数、根路径、原题、次数、源码/模型/索引清单和 Semble SDK parity；不直接采用已保存的评分。

安装、建库或产品调用失败会使工作流失败，已审题目的未交付结果记零。格式不兼容、身份漂移、缺失/重复调用、篡改参数或评测器错误属于无效实验，完整聚合分数为 N/A。完整性及测量样本校验是 CI 门禁，质量数值暂不设置任意阈值。

## 报告与 artifacts

- `retrieval-results`：唯一概览 `summary.md`、`summary.json`，两端有效时附 `comparison.json`。
- `retrieval-zg-report`：ZG 完整 `report.json`、逐次 `scores.jsonl` 与简洁报告。
- `retrieval-data-<owner>__<repo>`：ZG 每仓库的原始响应、请求、安装和索引证据。
- `retrieval-semble-evidence`：仅启用 Semble 时生成，包含全部原始响应、清单和 SDK parity。

ZG 报告 schema 3，Semble 报告 schema 2：`file_retrieval` 保存文件指标，`semble_official` 仅保留 nDCG@10，`measurements` 保存两项操作测量及样本数。每个质量观测附五次 `measurement_observations` 以便重算。旧 anchor 顶层字段、旧 summary 和分类分数不再输出。

离线比较仍能读取旧 ZG schema 1/2，但只从 public items 重算当前指标；缺少完整测量证据时显示 N/A，不盲信旧缓存。每次原始数据重放与新的产品/CI 执行应明确区分。
