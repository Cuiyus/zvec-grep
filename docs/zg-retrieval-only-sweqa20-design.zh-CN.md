# zg Retrieval-only：SWE-QA 20 题

实现日期：2026-09-17。执行入口、评分器、20 题 Gold、离线重评分与 CI 工作流已实现。运行说明见 [benchmark README](../benchmarks/zg-retrieval/README.md)，工作流见 [Retrieval-only](../.github/workflows/retrieval-only.yml)。实际运行是否成功以对应 `report.json` 的完整性结果为准。

目标是固定问题、源码与标签，测量有效源码入口能否被返回、排名是否靠前、相同请求能否复现。每题只使用原始问题，不增加子 query，不运行回答 Agent。它是受控的组件测试，不能称为历史 Agent 请求的逐条回放。

## 数据与标签

题目来自 [Actions 35206585943](https://github.com/Cuiyus/zvec-grep/actions/runs/35206585943)，冻结来源为 `57713b431d09144426ec08747a4eea546f82c184` 的 SWE-QA selection，题库为 `peng-weihan/SWE-QA-Bench@c13deac7a0d99b0ca2e593e004c4739475785b08`。完整原题、UTF-8 hash、11 个源码提交和来源指纹保存在 [source.lock.json](../benchmarks/zg-retrieval/data/source.lock.json)，原题逐字副本在 [queries.jsonl](../benchmarks/zg-retrieval/data/queries.jsonl)。

| 类别 | task_id |
| --- | --- |
| what | reflex:6、sqlfluff:2、conan:1、pylint:10、pylint:9 |
| where | sympy:38、conan:39、xarray:46、astropy:38、matplotlib:37 |
| how | streamlink:14、conan:19、django:21、pylint:14、requests:16 |
| why | django:32、xarray:32、streamlink:43、sympy:26、conan:27 |

20 题均保留；每类 5 题，来自 11 个仓库，属于公开开发/回归集，不是独立盲测。每次检索整仓库，沿用产品自身过滤规则；仓库内合法 tests/docs 不统一排除。题库中的 `source_file` 是问题来源，不是答案源码路径。

[Gold v1](../benchmarks/zg-retrieval/gold/v1) 有 20 个 reviewed 标注、60 个目标（含单列的 bridge），其中 12 题启用互补入口组 nDCG。两个独立 AI agent 分别提议、互相对照固定源码复核；没有冒充人工审查。标注过程中未根据当前 zg 输出挑选标签。每个目标保存完整文件 hash、精确行锚点及 hash、符号名、相关性理由和 proposer/reviewer。

参考答案仅提供定位线索；其错误被记录到 review notes。例如 Reflex 的 `needs_update` 实际检查更新间隔；Django 的 formset save 本身不创建 atomic；xarray 的 numeric-only 过滤与 reduction 逐变量交错执行。没有用历史答案 Judge 分数生成检索标签。

`accepted` 目标是 OR：找到任一直接有效入口即命中。仅有引导作用的目标标为 `bridge`，不计主分。这是部分正例标注，不宣称找齐答案所需的全部证据。后续发现漏标，应独立核验、升版 Gold 并重算全部配置，不只给发现新入口的版本补分。

源码文件 hash 使用原始字节，锚点按 LF 规范化换行但不删除缩进或改变正文。测试明确覆盖 CRLF。函数/类声明可以作为调查入口；完整长函数正文不是入口命中的先决条件。

## 执行协议

配置见 [protocol.json](../benchmarks/zg-retrieval/configs/protocol.json)。

1. 校验原题、Gold 和固定源码 SHA；拒绝未复核标签、源码漂移与 corpus 越界。
2. 在独立 consumer 安装待测 npm tarball，记录包 hash、版本和实际依赖锁文件。历史 run 的 source-built 0.2.1 与当前 candidate 分开标识。
3. 用 `zg install --target opencode --yes --mcp-transport stdio` 生成官方配置；固定 MCP 客户端执行其生成的 command，不改写安装指令或增加自定义 bridge。
4. 每个仓库建立全新索引，使用 `local/potion-code-16m-v2`、CPU；模型缓存可以复用，索引不缓存。准备阶段与检索计时分开。
5. 通过公开 `zvec_grep_search` 调用 `root=<实际固定源码绝对路径>, query=<原题>, limit=10, autoUpdate=false, freshness=eventual`。本地/CI root 记录在 manifest，不冒充历史 `/app`。
6. 每个仓库在同一索引和已就绪 MCP 会话中按固定题目顺序执行：每题 `preview:short` 连续 5 次，再 `preview:full` 连续 5 次。两组各取第 5 次评分，全部重复用于稳定性；每组仍为 20 个质量样本。首个会话查询标记单列。
7. 可选 FTS/vector 消融使用同一原题，分别仅传 `fts:[Q]` 或 `vector:[Q]`，不同时传 `query`。全部 hybrid 轮先完成，不挑三模式最高分代表产品。

主回归是 200 次调用，全三模式是 600 次调用；每组质量样本仍是 20 题。题目、Gold、安装配置和报告均在搜索 corpus 外。源码自身 `.zvec-grep` 是产品索引目录，扫描清单会校验它没有被索引。

`short` 保持产品默认的有界摘要；`full` 返回每条检索结果已有的完整源码内容和 outline，不增加整文件读取，也不能恢复建库时已经省略的内容。两组请求只差 `preview`，保持同一原题、Top-10、索引和检索模式。报告逐次比较公开的原生排名、路径、范围、matched range 和 match type；若不同，会明确标记检索差异，不能将锚点分数变化全部归因于展示长度。

## 指标与匹配

主评分仅使用公开 MCP 原始文本。后台结构化源码、候选和持久化片段不能替代实际可见内容得分。评分器保留原生 rank，不按文件压缩排名。

| 指标 | 定义 |
| --- | --- |
| Semble 官方算法 nDCG@5/10（主指标） | 对 20 题的 39 个冻结文件目标使用 Semble 原生首次命中排名、二元收益和目标数 IDCG；主汇总为仓库宏平均，另列 query mean 和语言宏平均 |
| Hit@1/5/10 | 对应原生前 K 项中至少命中一个 accepted 入口 |
| 首次命中排名 | 首个有效入口的位置；前十没有记 `not_in_top10` |
| RR@10 / MRR@10 | 首次命中为 r 时 1/r，否则 0；主表逐题等权平均 |
| 补充 nDCG@5/10 | 只在预先标注互补证据组的 12 题上计算，单独公布分母 |
| 排名重复性 | 5 次可见路径、range、matched range、源码位置和 outline 身份一致性；不声称取得隐藏 entity ID |
| 文本重复性 | 5 次完整原始可见文本 SHA256 一致性 |
| short/full 配对一致性 | 同题同轮的检索身份和 Semble nDCG 是否一致，源码及 outline 展示差异不算排名变化 |
| 可见输出字节 | 实际 MCP 公开文本的 UTF-8 字节数，仅表示输出体积，不等于 token 或回答质量 |
| 延迟与准备耗时 | 每次 MCP 往返、首次连接、完整索引构建；5 次重复不用于可靠尾延迟结论 |

原有 Hit/MRR/互补组 nDCG 作为源码锚点诊断保留。这里的符号命中需要实际可见的任一完整已标注锚点（声明或关键正文），或精确定位到该定义的首部 outline；代码段需要完整锚点可见。仅路径相同、父类范围覆盖、隐藏正文含目标、同文件另一函数或被裁剪的锚点都不命中。AST 起点导致首行缩进被省略时，仅在精确源码起点允许这一已知渲染转换。

Semble 主指标不使用上述锚点可见性约束：当前文件级标签只要求路径匹配，并保留原生结果位置。排名相同时 short/full 的主指标应相同；锚点诊断则可能因展示更多源码而上升。公式与归一化由固定版本 Semble 原始 Python 函数对照验证，数据集仍为 SWE-QA，并非 Semble 自带题集。

原有互补组 nDCG 支持 0/1 标签，不要求等级标签或全仓穷举。每个互补组至多贡献一次，每个原生结果位置至多贡献一次；用最大折扣的一对一匹配处理重叠，避免重复入口和贪心匹配造成虚高。等价入口在同组。位置 i 的收益为 `1/log2(i+1)`，分母为最多 K 个已标注组的理想收益；它不是回答准确率或完整证据召回率，也不替代 Semble 主指标。

同一表格并列 short/full 的 Semble 主指标和旧锚点指标。旧指标按题等权，另列 what/where/how/why、逐题及仓库准备信息。旧 Hit 指标中一题即 5 个百分点，每类别一题即 20 个百分点。未添加字节窗口、首命中字节或 Agent token 节省指标。固定先 short 后 full 会产生预热顺序效应，因此不以两组延迟直接判定速度优劣。

## 完整性与异常

- 产品安装、建库或调用失败：已 reviewed 题作为未交付入口计 0，并使 CI 运行完整性失败；不能删除失败题提高平均分。
- Gold unknown/disputed：指标 N/A，并显示覆盖率；当前冻结集是 20/20 reviewed。
- 格式不兼容、源码身份错误、缺调用、重复调用、请求改写或评测器失败：实验无效，拒绝发布完整聚合主分。
- 每个 task/mode/preview/repetition 必须有独立原始响应文件及捕获 hash。离线重评分会重验请求字段、根路径、文件名、hash、次数和身份，不直接信任已保存分数。缺任一对比组会使实验无效。
- 成功准备必须有 before/after 阶段清单、文件与模型指纹、索引逻辑内容 hash，以及完整性验证记录。缺证据不能靠填写 finished 状态通过。
- 初始 CI 只阻止无效或失败的实验；质量分数报告，不预设 90% 等任意阈值。回归门槛应由基线、噪声和产品容忍度决定。

## 可观测范围

阶段产物包括固定源码文件清单、以同一产品策略重扫的文件清单与有限跳过原因、完整只读持久化片段及向量 hash、源码行映射和最终公开输出。快照使用全量迭代并核对 docCount，不用 top-k 结果冒充全量索引。

索引身份包含文件记录、配置、片段字段和向量内容；明确排除时间戳、锁、日志及存储物理布局。before/after 对比证明本次固定请求期间相关内容是否改变，不宣称不同平台构建能得到逐字相同的索引。

实际 embedding 输入、完整候选池和融合/选择中间态目前不可观测，报告明确标记 `not_available`。同文件相似文本不算源码载体：必须核验片段 range 对应的逐行内容；outline 无法映射时记不确定。不凭“vector 未命中”断言 embedding 有问题，也不对多个替代目标强行归纳成一个根因。

若以后专门比较 embedding 输入或 chunking 算法，需要补齐对应输入旁路；本实现没有伪称已取得该能力。

## CI

独立 Retrieval-only 工作流接入相关代码、包、协议与工作流变更的 PR/main push；手动运行可以选 hybrid 或全三模式。CI 为 Ubuntu 24.04 / Node 24，先运行协议/评分测试，再构建一个候选包，按 11 个仓库分片运行（最多 4 并行），最后严格要求全 20 题完成。

失败也上传原始响应和阶段证据，聚合报告写入 GitHub Summary。consumer 依赖及 runtime-home 不上传，模型权重也不上传，只保留指纹。下载全部 `retrieval-data-*` artifact 后可离线重评分。报告自身区分 full 与明确选择的 subset；单题 smoke 不能冒充完整评测。
