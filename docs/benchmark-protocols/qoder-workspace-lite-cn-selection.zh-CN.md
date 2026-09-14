# Qoder + Qwen3.8-Max：Workspace-Bench Lite CN 选题协议

研究日期：2026-09-14。本文记录选题、评测边界与准备阶段验证；不代表已取得 QA 收益结论。

## 选择与范围

选择用户列出的 **Workspace-Bench Lite CN**，冻结下方 10 个原始 task。用户已确认允许代码和文档/工作区的广义只读 QA。这里“只读”指保留输入资料和代码；原题要求的新建答案文件仍是交付物。三题直接涉及代码/项目 QA，七题涉及文本资料、JSON 与 CSV 的分析。结果应称为“Workspace-Bench Lite CN 的 10 题 QA 子集”，不能称为完整 Lite 成绩，也不能外推为 10 题纯代码 QA 的收益。

选择规则在看到本次模型结果前确定：原任务以资料理解、定位、核对、汇总或解释为主；输入依赖为文本/代码/CSV；交付物为 Markdown/TXT；不要求修复源代码、重排文件、实际发送消息或生成 Office 文件。该子集用于先验证流程和量化收益方向，既不按 zg 是否获胜筛题，也不按官方难度分布加权。当前流程 case 选 **3**；128 的两次准备阶段失败及调整依据记录如下，正式 10 题保持不变。

## 五个候选的比较

| Benchmark | 公开事实与可用性 | 本轮判断 |
| --- | --- | --- |
| Terminal-Bench Pro Private | Pro 400 题中公开、私有各 200；公开部分兼容 Harbor。私有部分官方只公布向维护者提交 API access 的代评路径，未承诺允许自带 Qoder、安装 zg 或导出逐次 usage。[官方说明](https://github.com/alibaba/terminal-bench-pro#3-submission-guidelines) | 未获得私有数据和完整 A/B 接口前不选；不能把 Public 当 Private。 |
| Workspace-Bench Lite CN | Lite 为 100 题；工作区有多种文件与干扰信息，中文 metadata、rubrics、输入材料均公开。runner 和 judge 可以分开执行。[官方仓库](https://github.com/OpenDataBox/Workspace-Bench) | 与检索、跨文件解释最接近，选取下方原始 QA 题。需要接入 Qoder adapter。 |
| DeepSWE v1.1 | 113 个长程工程任务、五种语言；v1.1 收集 agent 提交的补丁，在独立干净容器运行行为验证；要求 Pier > 0.3.0。[官方任务与验证说明](https://github.com/datacurve-ai/deep-swe#task-format) | 验证目标是代码实现正确性，不能在保留原评分的同时改成只读 QA。 |
| QwenClawBench v1.1 | 100 题、8 类，以 OpenClaw 模拟工作区为中心；自动、LLM、混合三类评分。原始任务包含配置修复、cron、工作流等操作，官方说明曾用于 Qwen3.6-Plus 开发期内部测试。[官方说明](https://github.com/SKYLENAGE-AI/QwenClawBench) | 有审计相关题，但需处理 OpenClaw 行为和 Qoder 的差异，不能把该集当作已证实的 Qwen 未见集；本轮不选。 |
| NL2RepoBench | 从自然语言需求和空工作区构建完整可安装 Python 仓库，以测试通过率评价。[原论文](https://arxiv.org/abs/2512.12730) | 原始目标是仓库生成，不适用于只读 QA。 |

以上是依据公开任务定义作出的适用性判断，不是业务团队内部版兼容性的证明。若业务团队提供的数据版本或 judge 不同，应更换来源记录并重新核对，不能沿用公开版本标签。

## 冻结来源与工作区

- Workspace-Bench runner 检查快照：`3fbd0f1a136720fece86786545983e26642c3db2`。[固定提交](https://github.com/OpenDataBox/Workspace-Bench/tree/3fbd0f1a136720fece86786545983e26642c3db2)
- Lite dataset 检查快照：`60b08b1cc2e8054afbc3ca2160d37876b4f0765c`。[中文 metadata 表](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/60b08b1cc2e8054afbc3ca2160d37876b4f0765c/task_lite_clean_cn_metadata_table.csv)
- 逐题原始记录：`task_lite_clean_cn/<absolute_id>/metadata.json`；保留 `task`、`output_files`、`rubrics`、`rubric_types` 原文及文件 SHA-256。
- 完整可见工作区来自单独的 [Workspace-Bench-Workspaces dataset](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Workspaces)，中文 archive 为 `filesys_cn.zip`；工作区 dataset revision 为 `e245d63bfa20cfdb708cd8e78145ffb087155857`，archive SHA-256 为 `4d04f93233664b159620dee07b17c35cc4984a220c12b5d3e3db759146b82bee`，压缩文件为 18,861,940,415 bytes。官方 [downloader](https://github.com/OpenDataBox/Workspace-Bench/blob/3fbd0f1a136720fece86786545983e26642c3db2/evaluation/scripts/download_hf_assets.py) 可用 `--language cn --lite --workspaces` 下载。

每次 rollout 从对应 persona 的官方完整工作文件集合开始，包括无关文件。统一排除路径组件为 `.git` 的版本控制元数据，保留 `.gitignore`、`.github` 和仓库工作文件。这项全局规则独立于任务相关性，不使用 gold 选择保留文件。Research persona 原 archive 含 654 个 `.git` 文件、约 4.013 GB；排除文件数与字节数记录到 dataset manifest，随后为全部保留工作文件建立独立快照。该语料是排除版本控制数据库后的完整工作文件集合，应按此命名。`data_manifest` 是评测方的依赖描述，不能只给 agent 这些文件；否则移除了检索干扰因素，且透露了相关文件集合。不能向 agent 或 zg 索引暴露 metadata、rubrics、依赖图、其他任务答案、历史 rollout 或 judge 日志。

官方在 2026-08-17 修复了任务外部 metadata 泄漏问题，必须使用包含该修复的 runner 和等效隔离。完整工作区里原有文档可以保留；评测专用 rubric 数据必须在 agent 容器外。原始输入文件的修改、删除与新增输出应独立核查，不能用 exit code 推断只读合规。[官方修复说明](https://github.com/OpenDataBox/Workspace-Bench#-news)

## 10 个任务

以下原文来自固定版本的逐题 metadata，仅用于审阅选题。运行时应读取该版本的 `task` 字段，并应用固定 runner 的官方 task patch（本子集仅 127）；不把本文件的选题理由、参考文件数、rubric 数量或后文审计发现注入 agent。

| Task ID | 类型 | 为什么纳入 | 原始答案文件 | Rubrics |
| --- | --- | --- | --- | --- |
| 3 | 代码与依赖 QA | 37 个依赖文档/清单；跨 Markdown、package.json、pom.xml 提取与去重，不修改项目配置。 | `project_dependencies_unique_list.md` | 21 |
| 127 | 代码依赖 QA | 8 个 manifest 源文件；区分第三方包与标准库、导入名与安装包名，生成依赖说明。 | `requirement.txt` | 21 |
| 128 | 代码使用与参数 QA | 5 个 Python 源文件；解释脚本入口、参数、默认值与运行关系。 | `ST-Raptor运行指令与参数说明.md` | 17 |
| 139 | 文档检索与事实汇总 | 60 个 Markdown 活动计划；定位日期与地点并归并，需要覆盖全部相关文档。 | `行程安排.md` | 25 |
| 143 | 结构化资料分析 | 100 个 JSON 社媒记录；比较平台和内容表现，解释点赞、互动与曝光指标，不发布内容。 | `social-media-post-summary.md` | 24 |
| 158 | 沟通记录分析 | 3 份 Markdown 用户反馈；将需求关联到部门、优先级与计划，只交付分析报告。 | `运营团队工作计划.md` | 22 |
| 160 | 多来源事实对齐 | 3 个不同日期的 Markdown 客户文件；按 ID 合并、按时间处理冲突，保留互补信息。 | `客户信息统计.md` | 20 |
| 161 | 跨文件关系推理 | 2 份 Markdown 活动与团队文档；关联任务、部门、负责人、联系方式和时间，不执行活动。 | `实施清单.md` | 24 |
| 191 | 产品技术知识 QA | 5 份 Markdown 智能清单材料；梳理权限、工具接入、运营、改进与评测流程，不执行接入。 | `智能清单持续运营工作流程.txt` | 23 |
| 373 | 结构化文本检索与汇总 | 20 个 CSV 清单；筛选部门记录、按状态统计、人员去重，保留原 CSV。 | `Logistics_Dept_Summary.md` | 20 |

合计 217 条原始 rubric，锁定 243 个输入文件的原始 SHA-256。任务数少、各题 rubric 数不同，应同时报告按 task 宏平均与按 rubric 微平均，清楚标注分母。

### Task 3 原始中文指令

我需要一份项目依赖关系的概览。从相关文件中提取出项目所依赖的主要库或框架的名称，然后去重，生成一份 project_dependencies_unique_list.md。

[原始 metadata](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/60b08b1cc2e8054afbc3ca2160d37876b4f0765c/task_lite_clean_cn/3/metadata.json)

### Task 127 原始中文指令

测试项目文件夹的python目录下有10个python文件，帮我总结并生成一份requirement.txt文件

固定 runner 官方 patch 后的实际指令：测试项目文件夹的python目录下有8个python文件，帮我总结其中使用的第三方依赖并生成一份 requirement.txt 文件。

[原始 metadata](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/60b08b1cc2e8054afbc3ca2160d37876b4f0765c/task_lite_clean_cn/127/metadata.json)

### Task 128 原始中文指令

我的ST-Raptor项目文件夹下有5个以各自功能所简要命名的.py文件，我可能会通过不同参数多次运行，给出运行的终端指令模版，并列出完整参数列表，总结成说明，输出为ST-Raptor运行指令与参数说明.md文件

[原始 metadata](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/60b08b1cc2e8054afbc3ca2160d37876b4f0765c/task_lite_clean_cn/128/metadata.json)

### Task 139 原始中文指令

在活动计划目录下是公司最近的日程计划，我需要一份简要的日程计划表行程安排.md打印发到员工手中，以便他们能够快速知晓每日前往的地点，以日期-地点的简要格式呈现

[原始 metadata](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/60b08b1cc2e8054afbc3ca2160d37876b4f0765c/task_lite_clean_cn/139/metadata.json)

### Task 143 原始中文指令

我的自媒体目录下是我近期的一些社交媒体发布数据，现在需要一份报告social-media-post-summary.md,放到同目录下，根据点赞、互动、曝光率等指标看看什么平台什么内容效果最好

[原始 metadata](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/60b08b1cc2e8054afbc3ca2160d37876b4f0765c/task_lite_clean_cn/143/metadata.json)

### Task 158 原始中文指令

根据运营团队的部门分工，结合交流记录文件夹下有3份关于最新产品的用户沟通记录，为我总结一份未来运营团队的工作计划，通过划分优先级以及给出具体实施方案，列出待办事项清单，辅助未来运营团队的工作，输出运营团队工作计划.md文件

[原始 metadata](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/60b08b1cc2e8054afbc3ca2160d37876b4f0765c/task_lite_clean_cn/158/metadata.json)

### Task 160 原始中文指令

用户文件夹下有3份不同渠道日期用户信息文件，按照用户信息文件中的用户ID归类，并以按照新日期更新用户信息，汇总生成一份用户信息统计表，并输出为 客户信息统计.md 文件

[原始 metadata](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/60b08b1cc2e8054afbc3ca2160d37876b4f0765c/task_lite_clean_cn/160/metadata.json)

### Task 161 原始中文指令

活动文件夹下有团队分工和活动方案文件，结合两个文件为我制定一份具体实施的工作清单，要求能明确划分子任务到各个部门，明确各任务时间，并附上负责人和联系方式，输出到活动文件夹下，文件名为实施清单.md文件

[原始 metadata](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/60b08b1cc2e8054afbc3ca2160d37876b4f0765c/task_lite_clean_cn/161/metadata.json)

### Task 191 原始中文指令

基于智能清单，弄一份，智能清单落地、持续改进的工作流程，输出在/桌面/智能清单改进工作流程，并且生成智能清单持续运营工作流程.txt这个文件。

[原始 metadata](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/60b08b1cc2e8054afbc3ca2160d37876b4f0765c/task_lite_clean_cn/191/metadata.json)

### Task 373 原始中文指令

统计“物流部”的所有业务运行情况。需遍历目录下所有 CSV 格式的清单文件，提取所有所属部门为 '物流部' 的记录，按 '状态' 进行分组统计（计数），并列出所有涉及的人员名单（去重），最终生成 'Logistics_Dept_Summary.md'。

[原始 metadata](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite/blob/60b08b1cc2e8054afbc3ca2160d37876b4f0765c/task_lite_clean_cn/373/metadata.json)

## 保留原题与原评分

本次实现采用只读 QA 的答案交付适配：保留原始中文任务文字，并添加两组一致的说明，要求 agent 在最终回复中给出完整报告；harness 将该回复原样保存为题目要求的答案文件，保存位置在只读 source corpus 外。原始输入代码和资料保持不变。最终回复、落盘文本及 SHA-256 应同时保留，确保没有补写、修饰或插入答案。

原始 rubric 及其类型保持不变，但“文件存在、命名、落盘”的动作由 harness 完成，不能声称 agent 在原始交互流程中独立完成这些动作。结果应标记为 **Workspace-Bench Lite CN QA 交付适配子集**，并分别披露全部原 rubric 得分与文件交付条目由 harness 执行的事实；该分数不能直接当作官方原生 harness 的完整 Lite 成绩。judge 读取原题指定文件名的候选交付物，不能通过把最终消息缺失当空文件而制造成功交付。

官方 rubric judge 使用 ClaudeCode harness，需要 Anthropic-compatible endpoint，对应 `JUDGE_BASE_URL`、`JUDGE_MODEL`、`JUDGE_API_KEY`。runner 报告“运行完成”与 rubric 得分是不同结果。Qoder 不是当前官方枚举的内置 harness，接入实现必须保存 Qoder 自身的任务轨迹、最终 usage 和输出文件，并将同一输入资料、输出资料、原始 rubric 交给固定 judge；不能改用关键词评分并沿用官方 judge 名义。[官方运行与评分流程](https://github.com/OpenDataBox/Workspace-Bench#-quick-start)

评分细节应在实际配置中记录 judge model/version、prompt、temperature（若服务支持）、并发与失败重试政策。baseline 与 with_zg 用同一个 judge，不向 judge 提供组别、zg 名称或成本比较。judge 的 token、调用、耗时单列，不计入 agent 效率指标。

## 已发现的题目限制

- 127 的原始 dataset 指令写“10 个 python 文件”，依赖 manifest 与 rubric 写 8 个；固定 runner 的 [官方 patch](https://github.com/OpenDataBox/Workspace-Bench/blob/3fbd0f1a136720fece86786545983e26642c3db2/evaluation/task_patches/lite_cn/127/patch.json) 已将任务改为 8 个文件并明确提取第三方依赖。运行两组均应用这个官方 patch，保留原始 metadata hash、patch hash 和实际指令。patch SHA-256 为 `863628a2af769b9f50dedd62993d55417bf35bc8e3cb7c662d570a6ee9611448`；它只修改 task，不修改 rubric。
- 139 的 rubric 包含日期字典序、特定标题与行顺序，比原指令更具体。不能把这些隐藏要求加到 agent prompt。
- 158、161 的 rubric 对优先级、日期、任务分配等有细粒度规定，其中部分可能允许语义等价答案。该事实不等于已经证明 rubric 全部有源文件依据。
- 191 的 manifest 中为 `智能清单体验说明.md`，部分 rubric 提及 `智能待办工具体验说明.md`；要保留原始文本并用实际内容审计。
- 在任何 rollout 之前发现 226 的四个 Python 文件与完整工作区同名文件的大小和 ZIP CRC 均不一致，因此排除 226，以 143 替换。143 的全部 100 个 JSON 输入与完整工作区同名内容的大小和 CRC 一致；最终 243 个输入均完成该交叉核验。实际提取后仍逐文件验证 SHA-256，不能把大小/CRC 校验写成完整源文件 SHA-256 已验证。
- 官方 CN grounded rescoring 自称初步输入依据分析，并非全部保留项都必需或接受语义等价答案的最终认证。原始得分始终保留；若给出 grounded 敏感性分析，要单独命名、冻结 audit CSV 版本和覆盖比例。[官方 grounded rescoring 说明](https://github.com/OpenDataBox/Workspace-Bench#preliminary-grounded-rescoring)

为控制文件格式和任务歧义，未采用 53（metadata 的输出文件列表与输入文件名重合）、55/95（答案文件要求 `.doc`）、284/287（依赖 Excel 源文件）；未采用 7/131/146/154/372（需要文件复制、整理或移动）、116（包含向群组发送消息）、286（实现与重构代码）。这些排除发生在本次 rollout 之前。

## 执行与观测口径

CI 分为五个显式范围：普通提交仅做离线校验；`[workspace-qa-probe]` 只用一个合成小文件验证真实 embedding、SDK 和 Qoder MCP 链路，不下载完整工作区、不运行 benchmark QA 或 judge；`[workspace-qa-rejudge]` 复用冻结 smoke artifact，仅恢复未合法的评分；`[workspace-qa-smoke]` 运行 1 题 × 2 组 × 1 次；`[workspace-qa-full]` 先通过 smoke，再运行 10 题 × 2 组 × 10 次。手动入口支持相同范围，默认 smoke。各范围使用独立并发组，正式评测不阻塞快速校验；probe、smoke 和评分恢复不混入正式统计。

准备阶段增加两类缓存。按固定 archive 版本和 persona 缓存已校验的 16 MiB 下载块，每 persona 最多 4 GiB；Research 工作区超过此上限，因此只承诺部分下载复用。索引缓存只保存成功构建并通过 preflight 的不可变 seed，按完整源文件内容、运行时版本、embedding 模型与 endpoint、索引配置和构建代码身份核验，命中后仍重新执行当前工作区 preflight。每个 with-zg trial 继续使用独立副本，不缓存问答、judge、session 或被查询修改过的副本。

缓存采用分开的 restore/save 步骤，后续 QA 失败时仍保存已下载块和已完成 seed。首次运行或缓存被淘汰仍会产生准备成本；不调整仓库缓存收费额度。range-cache-metrics.json 与 preparation manifest 分别记录下载命中字节和索引缓存命中、校验、原始构建来源。冷构建、缓存准备、Qoder 执行及 judge 耗时分开解释，不能把缓存构建耗时节约当作 zg 的答题效率收益。临时 Git 快照关闭压缩及自动 GC，文件内容和 Git 对象内容身份不变。

协议 v2 对全部任务统一设置 zg 原生 SDK 参数 `maxFileSizeBytes=1048576`，即只索引不超过 1 MiB 的文件。超过上限的文件整体跳过索引，不截断内容；它们仍完整保留在两组可读工作区中，可用 Read/Grep/Glob 访问。其余筛选沿用生产默认规则，不按题目、依赖清单或 gold 选择索引文件。这是显式资源配置，最终结果不能描述成无限制的默认索引配置。

调整发生在任何 Qoder QA 回答或 judge 分数出现之前。首次 [CI 34818892317](https://github.com/Cuiyus/zvec-grep/actions/runs/34818892317) 在索引阶段发现 SDK 缺少远程 operation permit，已修复并通过真实 SDK 的远程建索引和向量查询探针。随后 [CI 34821814894](https://github.com/Cuiyus/zvec-grep/actions/runs/34821814894) 在 1,800 秒索引准备预算处超时：1,226 个候选文件中完成 483 个，最后进度报告 0 个文件失败；进入 LongDA 的大型数据文件后明显变慢。该次没有进入 QA，不能据此声称模型答题失败或 zg 有/无收益。

据此冻结全局 1 MiB 索引上限，并把 smoke 从 Research 的 128 改为工作区较小的 Backend Developer task 3，以尽早验证完整问答和评分流程。正式任务仍为原 10 题。1 MiB 等于 zg 的代码文件默认上限，同时收紧 data/text 类型；不是按已知答案选取文件。两个准备阶段失败的 smoke 不进入 200 次正式 rollout 统计，原日志和修订依据保留。

随后 [CI 34828583811](https://github.com/Cuiyus/zvec-grep/actions/runs/34828583811) 完成两组回答和 judge，但原始日志显示唯一一次 zg MCP 查询因缺少 embedding key 而失败，成功检索为 0。此前只检查 agent 最终完成状态的 workflow 误判为成功；该次保留为集成诊断，不能当作有效 zg 收益比较。修复仅通过 MCP 配置中的 `${NAME}` 引用显式传递所需环境变量，不写入密钥值、不改变原题提示。新增下载前的微型真实 Qoder→MCP→远程向量检索探针，并要求 smoke 至少有一次 native 与 MCP 日志共同证实的成功检索。正式 batch 不强制调用 zg，也不筛除自主不使用 zg 的样本。

[CI 34831451904](https://github.com/Cuiyus/zvec-grep/actions/runs/34831451904) 的同 job 真实探针已成功检索，两组原题回答也完整，with-zg 工具已注册并 connected、各项完整性检查通过，但模型自主未调用 zg。原 smoke 门槛将自然不使用误判为接入失败；门槛 v2 改为分别核验真实探针、QA 工具注册与完整性、完整评分和指标。自然 0 次调用保留，不重抽问答；若原题中尝试 zg 却全部失败，仍不能通过 smoke。同时，with-zg 的 judge 输出重复至 8192-token 上限而截断，评分保留 null。新增 `[workspace-qa-rejudge]` 恢复入口，按冻结 artifact 的逐文件 SHA 复用原回答、轨迹和已合法评分，仅下载小体积的原始评分依据并补未合法的 judge；不重复完整工作区准备或 Qoder rollout。原始失败评分和验证文件另行保留。无效格式与瞬时传输异常共享固定最多 3 次尝试，恢复不重置预算；模型、问题、候选和 prompt 不变，首个合法评分立即接受，低分不重试。

该次恢复了 1,950,506,687 字节的 archive 缓存块，新下载块为 0；提取约 17 秒，前一冷运行约 194 秒，缓存归档本身恢复约 18 秒。修复 bridge 导致索引身份更新，本次仍花约 71 秒重建，完成 seed 在 judge 失败后仍成功保存。独立 [probe 34832632725](https://github.com/Cuiyus/zvec-grep/actions/runs/34832632725) 的实际模型与检索检查耗时 26.117 秒，冷启动 probe job 约 95 秒，均不计入 QA 指标。

恢复运行 [34833581759](https://github.com/Cuiyus/zvec-grep/actions/runs/34833581759) 仅用 17.974 秒补齐失败 judge，保留原两份回答、全部原始 attempt 和合法 baseline 评分，流程验证完整。随后首次 full [34833796405](https://github.com/Cuiyus/zvec-grep/actions/runs/34833796405) 的全新 smoke 出现 with-zg budget_exhausted，导致正式矩阵尚未启动；两类准备缓存均命中，索引构建耗时为 0，SDK 校验约 31 秒。该预算失败原样保留。full 启动现在按固定 31 个评测核心代码/配置文件的 SHA 校验是否与已通过完整验证的 smoke 一致：一致时恢复指定 artifact，校验原始证据并重算 gate/report；不一致时才跑新 smoke。显式 smoke 始终执行新样本。复用只涉及流程验证，正式 200 条仍是新会话，任务、模型、预算和原题不变。

固定 Qoder 版本、请求与实际解析的 Qwen3.8-Max 模型标识、zg `0.2.2`；按用户最新要求，embedding 使用 **远程 `qwen/qwen3.7-text-embedding`**。禁止静默回退到本地 embedding 或其他模型。记录实际 endpoint/provider、请求及解析模型标识、索引配置，以及 embedding 调用与索引准备耗时；凭据通过 CI secret 注入，不能写入快照或日志。不要把 zg 当前分支源码冒充 npm `0.2.2`。冷启动总时长、索引准备时长与 agent 执行时长分别报告；远程 embedding 的 usage 和耗时不能混入 Qoder 模型输入 token。

先用 3 在两组各跑一次，验证认证、实际模型标识、只读边界、答案文件、原始 rubrics 的 judge 和四项指标完整可观测。这个烟测用于修复流程；正式集参数冻结后，每个 task 在 baseline 和 with_zg 各重复 10 次，共 200 次 rollout，烟测不混入正式统计。每次使用独立任务容器、session 和 home，共享该 task 准备一次的只读源工作区；with-zg 使用不可变 seed 的独立副本。固定相同资源限制；交错运行两组并记录顺序，避免服务时段差异与缓存成为单组特征。

只读 QA 无须每轮重装环境、下载数据或建索引。当前同一 task 的 20 次正式测试共用准备成果；跨 task 仍是独立 CI job，通过相同 persona 的兼容缓存复用下载块和索引 seed，尚未合并成同一 runner。10 题对应 4 套不同语料工作区，软件运行时可以统一，但不能把所有题换成同一角色的文件。独立会话用于隔离上轮答案和执行状态，与重新下载文件是两回事。

逐次记录 task ID、组别、重复序号、agent 成功/失败状态、原始 judge 逐条判断和总分、input tokens、tool calls、elapsed seconds，并保留原始轨迹作为核算依据。agent 异常、judge 异常、缺失 usage 必须单列；缺失值用 null，不能填 0。input tokens 使用 Qoder 实际报告的累计输入，若无缓存分项或逐请求 usage 则标注不可用，不能按工具调用数推算。tool calls 明确为模型发出的全部工具调用数，并另列 zg search/rg 使用次数；同一批查询是否计一次按真实 tool-call ID 核算。

judge 分数、token、tool-call 和耗时按同一 task/repeat 配对比较，至少给出每题两组均值、差值与失败数，再给总体宏平均；小样本收益只作为探索结果。不能按 judge 成功过滤后只报效率均值而不披露失败率，也不能用 pass@10 代替本轮重复试验的平均质量。

实际运行仍需可用的 Qoder/Qwen3.8-Max 认证、judge 认证、GitHub Actions Docker/网络资源；这些是执行前置条件，不属于已取得的实验结果。
