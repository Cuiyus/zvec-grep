# 单题分析 CI

入口：[SWE-QA Case Analysis](../../../.github/workflows/swe-qa-case-analysis.yml)。默认 `stage=full`、`task=pylint-10`，完成 **baseline 十次 + 原版 ZG 十次 → 标准 Judge 评分 + 独立检索回放 → 上传证据**。诊断构建与 E2E 并行；评分和回放分别保存，任一步失败均不算完整交付。

## 怎么运行

1. 确认仓库已有 `GLM_API_KEY` secret 和相应模型服务配置。先将独立 workflow 合入默认分支，让 GitHub 注册入口。
2. 将 `candidate_ref` 填为本次最新主干的完整 40 位 SHA，选择 `stage=full`。执行期间保持该 SHA 不变。
3. 确认两组各十次完成、二十份答案均有 Judge 分数，下载 `case-evidence-*`、`case-judge-*`、`case-replay-*`。在本地 `benchmarks/swe-qa-bench/runs/<日期>_<题目>_<Agent-模型>_<embedding>_baseline-vs-zg_10x_<ZG短SHA>/` 保存原始产物、逐次 CSV、结果报告和 manifest。分别 Review 答案质量、资源对照和检索证据，确认后再扩大任务范围。

```bash
ZG_SHA=$(gh api repos/zvec-ai/zvec-grep/commits/main --jq .sha)
gh workflow run swe-qa-case-analysis.yml --repo Cuiyus/zvec-grep \
  --ref <已包含此工作流的分支> \
  -f candidate_ref="$ZG_SHA" -f task=pylint-10 -f stage=full
```

本试点通过 fork 上已注册入口分阶段执行，见 [真实运行记录](runs.json)。交付已恢复原 `swe-qa-bench.yml`，新增独立入口；**独立入口尚未合入默认分支，新的 `full` 串联入口尚未实跑**。正式 E2E、诊断构建、回放各阶段都已成功实跑，原版 ZG 未加入观测补丁。

## 分工与失败条件

| CI 自动完成 | 分析者完成 |
| --- | --- |
| 锁版本、十次隔离运行、完整会话与双向正文、源码哈希、索引归档 | 按固定源码确认题目需要的事实 |
| 全部唯一原始 query 各回放十次，检查原运行一致性、诊断公共输出一致性 | 看请求之前的上下文，逐项判断相关性和证据是否充分 |
| 召回、落选候选、融合/排序、预览对照、固定池评分对照 | 确认产品原因、保留成功反例，分开核对最终答案 |

缺会话/正文、源码变化、缺回放、诊断与原版输出不一致时，证据检查失败；失败产物仍保留。原版回放自身不稳定或与 E2E 不同会写入报告，不能删掉当作随机噪声。CI 成功仅表示自动证据检查通过；Harbor 的本题 verifier 只确认工作区存在，不能用 reward=1 当作答案正确。

## 证据怎么读

| 文件 | 用途 |
| --- | --- |
| `run-lock.json`、`evidence-manifest.json` | 版本、配置与原始文件校验和 |
| `jobs/*/*/agent/full-sessions.json` | 全部主/子会话、工具参数和正文、最终答案 |
| `wire/model/*`、`wire/mcp/*` | 原始请求、响应字节、完成状态；不保存鉴权头 |
| `query-audit.json` | query → 会话调用 → 请求前模型输入 → 下一次可见工具正文 |
| `production/<query_id>/01..10.json` | 原版十次独立检索回放 |
| `diagnostic/stages.jsonl` | 原生候选与存储的 FTS 文本、融合、完整候选内容、排序及选中项 |
| `summary.json`、`ranking-analysis.json` | 回放一致性、固定候选池消融；不能把续排位置当生产返回 |

`stage=preflight` 每组各运行一次并评分，不计入正式十次；`diagnostic-build` 可单独构建；`judge` 通过 `evidence_run` 对已保存的两组答案评分，无需重跑 E2E；`replay` 通过 `evidence_run`、`diagnostic_run` 重用既有产物，无需再调用模型。观测补丁与产物哈希均归档；不要把诊断二进制用于正式 E2E。
