# pylint:10 单题试点报告

**流程已跑通：10 次独立 E2E、16 条检索、140 次独立回放，确认两项 ZG 局部质量问题。** 本轮只分析 [#324](https://github.com/zvec-ai/zvec-grep/issues/324) 对应题目，尚未扩展其他 case。

| 检查 | 结果 |
| --- | --- |
| 正式运行 | 10/10 完成，10 个独立会话，源码前后不变 |
| 逐请求分析 | 16/16；去重后 14 条请求，169 个返回项、44 个不同实体均有标注 |
| 独立回放 | 14 × 10 次均稳定，与 E2E 返回一致 |
| 诊断可信度 | 14/14 与原版公共输出一致；全部候选排序可由离线评分器复算 |
| 模型实际可见正文 | 16/16 与 ZG 原始返回一致 |

配置：**OpenCode 1.18.4 + qwen3.8-max + local/potion-code-16m-v2（CPU）**；ZG 锁定开跑时主干 `891497fa87d3e03d0c9273fed0ba3e75af62f179`，Pylint 锁定 `44740e5e5cdd2b80321a4f0ed4254213d058b9f6`。94 次模型请求均记录了 temperature=0、seed=42、reasoning_effort=high；这是请求配置，不推定服务端实际采样方式。

## 确认的问题

### 1. 同文件多样性惩罚会挤出关键的第二个命中

首次检索中，`is_attribute_typed_annotation` **10/10 已召回，但仅 3/10 返回**。另 7 次中，固定候选池只移除 diversity 后，有 6 次进入各自请求组的返回窗口。

代表请求 `d71618a4012b2e8b`（发生两次，limit=10）：

| 阶段 | helper 的位置或变化 |
| --- | --- |
| 索引与切块 | 完整函数作为一个 fragment；存储的 FTS 文本包含 `AnnAssign` 判断 |
| 原生召回 → 融合 | FTS 第 31、vector 第 2 → 融合第 8 |
| 最终排序 | 同文件已有另一函数入选，分数 `.834638 × 2/3 = .556425`，诊断续排第 14，未返回 |
| 固定池对照 | 去 diversity：第 7；去 identifier 贡献：第 14；纯 RRF：第 8 |

这定位了该请求的损失发生在排序阶段，不能沿用旧 issue 的召回截断归因。**不建议直接关闭 diversity**：后续请求 `867c6ea614f724d8` 中，它将 helper 提升至返回第 8；移除后反而为第 15。其余一次首次遗漏移除 diversity 后仍在第 11，也不能单归因于这条规则。

### 2. 默认预览截掉了已命中的决定性条件

helper 在正式运行中共返回 **6 次**，均只展示源码 **1731–1740**，遗漏 **1741 行 `isinstance(attribute.parent, nodes.AnnAssign)`** 和后面的基类递归。只改 `preview=full`，6/6 恢复关键行，命中项和顺序不变。

损失发生在 ZG 展示窗口，传输和 OpenCode 没有再次截断正文。这里说的是该命中缺少关键条件，不代表整条响应没有其他相关证据。源码对应 [预览窗口选择](../../../rust/crates/zg-cli/src/preview.rs)；排序对应 [同文件衰减](../../../rust/crates/zg-engine/src/pipelines/indexed_search/ranking/mod.rs)。

## 十次运行与答案核对

题目主线是按 `AnnAssign` / `Assign` 做条件处理。[判定依据](source-rubric.json) 已同时核对 benchmark 参考答案与固定版本源码，允许等价证据。

| Run 后缀（按执行顺序） | 秒 | 检索数 | 首次返回 helper | 最终答案侧重 |
| --- | ---: | ---: | --- | --- |
| YyBMU6L | 162 | 2 | 否 | 归一化、注解识别 |
| 3tDtqsw | 139 | 1 | 否 | 归一化 |
| 6XoELcH | 99 | 1 | 否 | visitor、归一化、声明无值 |
| ekGjuDz | 128 | 1 | 第 12 | Pyreverse |
| dZRYmKH | 111 | 2 | 否 | visitor/target 分离、注解识别 |
| PFvP9Af | 102 | 2 | 否 | 归一化 |
| pCUARhw | 102 | 2 | 第 1 | 归一化、注解识别 |
| TGaFQep | 132 | 2 | 否 | 归一化；有一处事实错误 |
| 2BzvctA | 136 | 1 | 第 14 | Pyreverse；有一处事实错误 |
| NJUKjor | 138 | 2 | 否 | 归一化、枚举属性收集 |

10/10 都解释了节点类型分支；9/10 涉及赋值 target 归一化，3/10 涉及注解识别 helper，0/10 解释了参考答案中的 typed-annotation `no-member` 例外。**这不是 0/10 答对的判分**，而是事实覆盖记录。两处明确错误分别是“有注解就不会执行推断”和“`is_assign_name_annotated_with` 判断无 value”；源码均不支持，单独归入模型表述问题。

也保留了成功对照：`cd8e2309f3bfabb7` 将 `_get_all_attribute_assignments` 返回第 1；混合 query+fts 的请求也找到了完整归一化代码。10 次都继续使用了原生搜索/读取，但未做替换检索输出后的 Agent 对照，**不声称上述 ZG 问题导致了最终答案的具体遗漏或错误**。

## CI 工作量与交付

- 正式 E2E job **21 分 19 秒**；回放 job **1 分 14 秒**，回放不调用 Qwen。单次 E2E 中位数 **130 秒**。
- 模型 API 共 **94 次**（84 次主会话、10 次标题生成）；输入 **1,681,060 tokens**，其中缓存 **1,472,896**，未缓存 **208,164**；输出 **32,207**。未取得美元费用。
- 复用一次构建和索引种子，每次克隆隔离状态；相同请求去重回放。正式证据压缩包约 **29.7 MB**，回放约 **84.6 MB**。预检及开发失败单独保留，不计入十次。
- [CI 使用说明](workflow.zh-CN.md) · [逐请求索引](queries.zh-CN.md) · [完整标注与十份原始答案](review.json) · [执行记录](runs.json)
- [正式 E2E](https://github.com/Cuiyus/zvec-grep/actions/runs/40892707202) · [正式回放](https://github.com/Cuiyus/zvec-grep/actions/runs/40896401828) · [本地完整证据](../../../benchmarks/swe-qa-bench/runs/pylint10-pilot/)

**边界：** 两项问题已在本题复现，尚不能称跨 case 泛化。未抓取运行时分词 token 或文档 embedding 输入，因此不推断分词/embedding 故障；诊断 `projection.text` 是存储的 FTS 文本，vector 路径返回该字段也不使它成为 embedding 输入。窗口以外的排名均是诊断续排，不是生产实际返回排名。
