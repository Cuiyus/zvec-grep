# Qoder + Qwen3.8-Max：Workspace-Bench Lite CN 选题协议

研究日期：2026-09-14。本文记录选题、评测边界与准备阶段验证；不代表已取得 QA 收益结论。

**协议更正：用户明确要求按 README 执行 `zg install` 标准安装。此前自定义 MCP bridge 的 probe、smoke、评分恢复和 full 全部降为历史诊断，不能验证或填充新实验。标准安装仍在实现与验证中，尚未通过新的完整 smoke；正式 200 次实验将从头开始。**

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

## 标准安装更正与当前状态

此前运行使用了发布版 zg `0.2.2` 的 SDK，但由 benchmark 自行构造 Qoder MCP 配置并接入自定义 bridge，没有执行用户要求的标准安装流程。真实调用过 zg SDK 或 bridge 检索成功，都不能替代 `zg install` 产生的配置、搜索指引和产品原生 MCP 路径。现更正为按照仓库 [README](../../README.md) 和 [Agent integrations 安装说明](../01-agents.md#install-an-integration) 执行：

```bash
npm install -g @zvec/zvec-grep@0.2.2
zg install --target qoder --yes
```

安装 target 是 `qoder`，即便命令行程序名为 `qodercli`。文档说明安装器管理 `zvec_grep` MCP 项、搜索指引、Qoder trust 和精确工具权限；Qoder CLI 使用 `${QODER_CONFIG_DIR:-~/.qoder}` 下的 `settings.json` 与 `AGENTS.md`，IDE 默认使用 `~/.qoder/mcp.json`，可通过 `QODER_IDE_MCP_PATH` 隔离。文档支持显式选择 stdio/HTTP transport，实际采用的选项必须记录。安装后启动新会话，才能证明 Qoder 加载了实际生成的配置和指引。不能用手写 MCP 条目或评测专用指引替换安装产物后，仍称为标准安装。

with-zg 各独立配置环境应来自实际标准安装，保存 release 身份、安装命令与退出状态、配置/指引哈希、客户端加载与工具注册证据。baseline 继续使用独立的干净配置。软件包和原始数据可复用，答案与 session 不共享；复用准备成果不免除验证标准安装产物的要求。安装器可能启动本地服务，应按实际 transport 检查就绪状态，参见 [安装验证](../01-agents.md#verify-the-setup)。

本地 MCP 工具许可与远程 embedding 数据授权是两件事。用户已指定本实验使用远程 `qwen/qwen3.7-text-embedding`；CI 应通过发布版支持的授权路径落实并记录该实验工作区的授权，另行注入 provider credential。不能将持有 API key 等同于原生 MCP 授权已经生效，也不能继续以 benchmark 私有 operation permit 证明标准安装链路成功。

**当前标准安装尚未验证成功。** 新流程应在 GitHub Actions 中依次完成离线校验、标准安装路径的小文件 probe、task 3 的全新两组 smoke，再运行正式矩阵。此前所有 bridge 的 probe/smoke/rejudge 成功标志和哈希记录只保留历史含义，不能复用为新流程验证。新正式实验重新执行 10 题 × 2 组 × 10 次，共 200 次；旧 baseline 和旧 with-zg 均不并入新结果。后续不得以恢复旧 bridge 的未完成样本来填充这 200 次。

## 保持冻结的实验条件

本次更正改变安装与接入方法，不因已观察到的结果调整任务、模型或预算：

- 原 10 题保持为 3、127、128、139、143、158、160、161、191、373，仍为 3 题代码 QA、7 题其他只读 QA。
- Qoder CLI `1.1.45`、请求与实际解析模型 `qwen3.8-max`、zg 发布版 `0.2.2`。
- 远程 embedding `qwen/qwen3.7-text-embedding`；GLM-5.2 按原 rubric 评分。禁止静默切换模型或回退到本地 embedding。
- baseline 与 with-zg 每题各 10 次；维持固定的 AB/BA 交错顺序、4 CPU / 8 GiB 容器限制，以及每次 QA 的 900 秒、60 次模型请求、120 次工具调用、600,000 inclusive input tokens 上限。
- 原始完整 persona 工作文件、逐文件 SHA 校验、统一排除 `.git` 元数据、原题提示及答案文件交付方式保持不变。
- 所有任务维持 1 MiB 索引文件上限；超过上限的文件整体跳过索引，在两组可读源工作区仍完整保留。其余筛选沿用发布版默认规则，不按题目、依赖清单或 gold 选文件。这不是无限制的默认索引配置。

每次使用独立任务容器、session 和 home，同一 task 可共享准备一次的只读源工作区；with-zg 的 index 状态继续隔离。只读 QA 无须每次重新下载原始工作区或重新安装 npm 包，但每个 Qoder 配置环境必须加载真实 `zg install` 的产物。10 题涉及 4 套不同语料，不能以统一为一个角色工作区减少准备成本。

## CI、缓存与结果边界

实际安装、准备及模型调用均通过 GitHub workflow 执行。范围继续区分 `validate`、微型 `probe`、`smoke`、`full` 及独立评分恢复 `rejudge`；在标准安装实现完成并验证前，不宣称这些新路径已经走通。`data/probe-validation.json`、`data/smoke-validation.json`、`data/judge-recovery.json` 当前记录的是旧 bridge 流程。未来若复用验证，必须另有标准安装协议自己的完整证据和兼容配置。

按固定 archive 版本和 persona 缓存已校验的 16 MiB 下载块，每 persona 最多 4 GiB；Research 工作区只能部分复用。原始语料缓存与安装方法无关，但使用前仍验证 CRC/SHA。索引 seed 只在完整内容、发布运行时、模型、endpoint、索引参数和准备方式兼容时复用，并重新验证；旧 bridge 成功不能替代新原生路径的兼容验证。不缓存问答、judge、session 或修改过的 trial index。冷下载、缓存恢复、索引构建、安装和校验时长分别记录，不作为模型答题效率收益。

新 smoke 要证明标准安装后的 Qoder MCP 链路可用：小文件向量检索成功、配置和工具实际加载、只读与结果完整性、评分及四项指标完整。原题里模型自主不使用 zg 仍是有效观察，不为了获得一次 zg 调用重抽样；已尝试却失败的调用不能计为成功。probe 与 smoke 均不进入正式 200 次统计。

## 旧 bridge 运行的保留与分类

完整分类见 [legacy-bridge-runs.json](../../benchmarks/workspace-qa/data/legacy-bridge-runs.json)。该标记保留原始结果和失败，不覆盖原 artifact，也不将历史成功重写成当时的技术失败；它表示这些记录不满足更正后的安装协议。

| 运行 | 原始观察；全部仅限旧 bridge 诊断 |
| --- | --- |
| [34818892317](https://github.com/Cuiyus/zvec-grep/actions/runs/34818892317) | task 128 索引阶段缺少 SDK remote-operation permit，未进入 QA。 |
| [34821814894](https://github.com/Cuiyus/zvec-grep/actions/runs/34821814894) | task 128 在 1,800 秒索引预算处超时，1,226 个候选完成 483 个，未进入 QA。 |
| [34828583811](https://github.com/Cuiyus/zvec-grep/actions/runs/34828583811) | 完成 task 3 两份回答及评分，但唯一 bridge 查询因 embedding key 缺失而失败。 |
| [34831451904](https://github.com/Cuiyus/zvec-grep/actions/runs/34831451904) | bridge 探针成功，两份 QA 完成且自然 0 次 zg 调用；一次 judge 输出截断。 |
| [34832632725](https://github.com/Cuiyus/zvec-grep/actions/runs/34832632725) | bridge 的合成文件探针完成，不能证明标准安装。 |
| [34833581759](https://github.com/Cuiyus/zvec-grep/actions/runs/34833581759) | 只补一次 judge，保留原回答和合法评分；通过的是旧 bridge 流程验证。 |
| [34833796405](https://github.com/Cuiyus/zvec-grep/actions/runs/34833796405) | 新 bridge smoke 的 with-zg 达到预算上限，正式矩阵未启动；保留原预算失败。 |
| [34837171877](https://github.com/Cuiyus/zvec-grep/actions/runs/34837171877) | commit `d1962c2107d80c8685acc5096d2e927972317a7c` 的 bridge full；更正时已请求取消，任何部分结果均归历史诊断。 |

前两次 task 128 准备失败之后、任何 QA 回答或分数出现之前，已冻结 1 MiB 索引上限并改用 task 3 smoke；该选择仍有效，原 127/128 没有移除。后续 bridge 的环境传递修复、自然不使用 gate、judge 恢复、缓存收益及 smoke 复用均有诊断价值，但不能转化为标准安装实验的通过证据或正式样本。

## 观测与恢复口径

逐次保留 task ID、组别、重复序号、judge 逐条判断和总分、Qoder 原生 inclusive input tokens、全部尝试的工具调用、zg 调用、elapsed seconds、原始轨迹及终止状态。缺失值为 null；预算失败、模型失败和未启动状态分别保留在计划分母中，不能写成零成本或用成功样本替代。cached input 已包括在 Qoder inclusive 计数中，不重复相加；embedding 和 judge token 不混入 QA 输入。

安装、索引准备、host 校验和 judge 时长单列。新的标准安装运行时需要重新验证计时边界，不能用旧 bridge 的启动、检索或内部完整性检查耗时证明原生安装延迟。正式运行开始后保持同一时间口径，不中途改动以追求完整结果。

GLM-5.2 使用原始资料和候选输出，盲于组别与成本指标，评分全部原始 rubrics；这仍是自定义适配器，不是官方 ClaudeCode 文件系统 judge。无效格式、截断或瞬时传输错误共用最多 3 次 judge 尝试；恢复时保留原尝试、候选、prompt、模型和合法评分，不重置预算，首个合法评分无论高低立即接受。合法模型失败和低分 QA 不重试。

新实验结果按同一 task/repetition 配对，题内取平均后跨题等权汇总，并区分代码 QA 与其他只读 QA。未完整执行时报告覆盖率、失败原因和缺失项，不宣称已完成 200 次或得出收益结论。新标准安装的真实验证和正式结果仍待 GitHub Actions 产生。
