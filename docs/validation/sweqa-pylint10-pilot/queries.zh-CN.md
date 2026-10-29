# 逐请求分析索引

**16 次调用全部覆盖；14 条唯一请求的返回项逐项核对。** 相同参数与结果合并展示，发生在哪些 run 仍逐一保留。完整请求前上下文与正文见 [review.json](review.json) 的 links，以及 [原始会话证据](../../../benchmarks/swe-qa-bench/runs/pylint10-pilot/formal-40892707202/case-analysis/query-audit.json)。

“核心”指本题参考机制；“相关”可提供等价机制或可继续追踪的线索；“弱相关”只有局部语义重合；“无关”不能回答当前请求。后续 outside-init 请求按自身目的分析，不能用原题三个函数的命中率代替其相关性。

| Query | 次数 | 返回项 | helper 排名 | 赋值归一化排名 |
| --- | ---: | ---: | --- | --- |
| `467981359ca2b8a4` | 1 | 10 | 未返回 | 未返回 |
| `143451e045375985` | 1 | 8 | 未返回 | 未返回 |
| `1eb3976e3eef05c5` | 2 | 12 | 未返回 | 未返回 |
| `8de411471803bc29` | 1 | 18 | 未返回 | 9 |
| `666323c9d1d01026` | 1 | 12 | 12 | 未返回 |
| `d71618a4012b2e8b` | 2 | 10 | 未返回 | 未返回 |
| `867c6ea614f724d8` | 1 | 8 | 8 | 未返回 |
| `cb3a08d07b730a79` | 1 | 8 | 3 | 未返回 |
| `1e759e0cc5a06879` | 1 | 10 | 1 | 未返回 |
| `e1e657bba1b5c72e` | 1 | 8 | 未返回 | 未返回 |
| `b562c4bd37f5e5e5` | 1 | 10 | 未返回 | 未返回 |
| `827adfa8796c5fd7` | 1 | 15 | 14 | 未返回 |
| `ea3a970daff34ec9` | 1 | 10 | 未返回 | 未返回 |
| `cd8e2309f3bfabb7` | 1 | 8 | 6 | 1 |

`8de411471803bc29` 包含两个 query group，limit=10 按组应用，合并后返回18项；不列为超限缺陷。`cd8e2309f3bfabb7` 的 top1 直接命中归一化函数，是成功对照。

## 展开单条请求

<details>
<summary>467981359ca2b8a4 · 1 次调用 · 10 个返回项</summary>

Run：YyBMU6L

原题的首次宽检索，寻找按 AST 节点类型区分属性初始化的机制。

```json
{
  "root": "/app",
  "query": "type-annotated versus non-annotated instance attribute initialization AST node type separation AnnAssign Assign",
  "preview": "full",
  "limit": 10
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [utils.py:1759](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1759) | 相关：按 AnnAssign 父节点识别指定注解，能证明节点分支；不直接说明实例属性或 no-member。 | — |
| 2 | [private_import.py:21](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L21) | 弱相关：片段是私有导入注解名称解析及赋值访问，AST词汇重合但对象不同。 | 片段从行中间开始 |
| 3 | [variables.py:2549](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/variables.py#L2549) | 弱相关：判断函数内变量的注解上下文，不是实例属性初始化。 | — |
| 4 | [checker.py:396](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/name_checker/checker.py#L396) | 相关：命名检查含 Assign/AnnAssign 分支；默认预览只到405，分支从408后开始。 | 片段从行中间开始 |
| 5 | [typecheck.py:1216](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L1216) | 弱相关：普通 Assign 的 visitor，未展示注解与实例属性的区别。 | — |
| 6 | [inspector.py:220](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/pyreverse/inspector.py#L220) | 相关：实例属性类型收集调用 infer_node，可继续追踪到 get_annotation。 | — |
| 7 | [class_checker.py:1726](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1726) | 相关：实例属性 target 的统一入口，可继续追踪 slots；片段本身未区分注解。 | — |
| 8 | [used_before_assignment_type_annotations.py:93](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/u/used/used_before_assignment_type_annotations.py#L93) | 弱相关：局部变量先使用后赋值的注解测试，不是实例属性。 | — |
| 9 | [redefined_variable_type.py:92](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/redefined_variable_type.py#L92) | 弱相关：普通赋值类型重定义检查，无 AnnAssign 对照。 | — |
| 10 | [private_import.py:134](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L134) | 相关：私有名称检查也按 AnnAssign/Assign 分支，可作类比；默认预览未显示该分支。 | 片段从行中间开始 |

</details>

<details>
<summary>143451e045375985 · 1 次调用 · 8 个返回项</summary>

Run：YyBMU6L

追踪 outside-init 与注解赋值 visitor；检索前尚未读取 checker 实现。

```json
{
  "limit": 8,
  "preview": "full",
  "query": "instance attribute defined outside init annotated assignment self.x: int handled separately from plain assignment visitor",
  "root": "/app"
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [class_checker.py:1192](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1192) | 相关：outside-init 检查入口及 instance_attrs 遍历，不直接按注解分支。 | 片段从行中间开始 |
| 2 | [private_import.py:217](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L217) | 弱相关：检查赋值是否访问私有名称，有无value判断但不区分实例属性初始化。 | — |
| 3 | [used_before_assignment_type_annotations.py:93](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/u/used/used_before_assignment_type_annotations.py#L93) | 弱相关：局部变量先使用后赋值的注解测试，不是实例属性。 | — |
| 4 | [typecheck.py:1216](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L1216) | 弱相关：普通 Assign 的 visitor，未展示注解与实例属性的区别。 | — |
| 5 | [features.rst:1390](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/doc/user_guide/checkers/features.rst#L1390) | 弱相关：变量赋值告警列表，不提供 outside-init 注解判断实现。 | — |
| 6 | [access_to_protected_members.py:195](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/a/access/access_to_protected_members.py#L195) | 相关：outside-init 正例，无注解对照。 | — |
| 7 | [mixin_class_rgx.py:33](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/m/mixin_class_rgx.py#L33) | 相关：mixin 配置与 outside-init 正例，无注解对照。 | — |
| 8 | [attribute_defined_outside_init.txt:1](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/a/attribute_defined_outside_init.txt#L1) | 相关：outside-init 诊断基准输出，只能验证该告警存在。 | — |

</details>

<details>
<summary>1eb3976e3eef05c5 · 2 次调用 · 12 个返回项</summary>

Run：3tDtqsw、TGaFQep

原题的首次宽检索，寻找按 AST 节点类型区分属性初始化的机制。

```json
{
  "root": "/app",
  "query": "type-annotated vs non-annotated instance attribute initialization AST node type separation AnnAssign Assign",
  "limit": 12
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [utils.py:1759](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1759) | 相关：按 AnnAssign 父节点识别指定注解，能证明节点分支；不直接说明实例属性或 no-member。 | — |
| 2 | [private_import.py:21](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L21) | 弱相关：片段是私有导入注解名称解析及赋值访问，AST词汇重合但对象不同。 | 片段从行中间开始 |
| 3 | [variables.py:2549](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/variables.py#L2549) | 弱相关：判断函数内变量的注解上下文，不是实例属性初始化。 | — |
| 4 | [checker.py:396](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/name_checker/checker.py#L396) | 相关：命名检查含 Assign/AnnAssign 分支；默认预览只到405，分支从408后开始。 | — |
| 5 | [typecheck.py:1216](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L1216) | 弱相关：普通 Assign 的 visitor，未展示注解与实例属性的区别。 | — |
| 6 | [inspector.py:220](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/pyreverse/inspector.py#L220) | 相关：实例属性类型收集调用 infer_node，可继续追踪到 get_annotation。 | — |
| 7 | [class_checker.py:1726](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1726) | 相关：实例属性 target 的统一入口，可继续追踪 slots；片段本身未区分注解。 | — |
| 8 | [redefined_variable_type.py:92](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/redefined_variable_type.py#L92) | 弱相关：普通赋值类型重定义检查，无 AnnAssign 对照。 | — |
| 9 | [used_before_assignment_type_annotations.py:93](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/u/used/used_before_assignment_type_annotations.py#L93) | 弱相关：局部变量先使用后赋值的注解测试，不是实例属性。 | — |
| 10 | [private_import.py:134](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L134) | 相关：私有名称检查也按 AnnAssign/Assign 分支，可作类比；默认预览未显示该分支。 | — |
| 11 | [code_style.py:19](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/code_style.py#L19) | 弱相关：大类片段涉及字典转换为 dataclass/NamedTuple，非实例属性初始化。 | 片段从行中间开始 |
| 12 | [typing.py:247](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/typing.py#L247) | 相关：AnnAssign 专属 visitor，处理冗余 Optional/Union 注解；支持分派但不是实例属性主线。 | — |

</details>

<details>
<summary>8de411471803bc29 · 1 次调用 · 18 个返回项</summary>

Run：6XoELcH

原题的首次宽检索，寻找按 AST 节点类型区分属性初始化的机制。

```json
{
  "root": "/app",
  "query": "type-annotated vs non-annotated instance attribute initialization AST node type separation AnnAssign Assign",
  "fts": "[\"AnnAssign\", \"ast.AnnAssign\", \"annotated attribute\"]",
  "preview": "full",
  "limit": 10
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [utils.py:1759](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1759) | 相关：按 AnnAssign 父节点识别指定注解，能证明节点分支；不直接说明实例属性或 no-member。 | — |
| 2 | [checker.py:396](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/name_checker/checker.py#L396) | 相关：命名检查含 Assign/AnnAssign 分支；默认预览只到405，分支从408后开始。 | 片段从行中间开始 |
| 3 | [private_import.py:21](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L21) | 弱相关：片段是私有导入注解名称解析及赋值访问，AST词汇重合但对象不同。 | 片段从行中间开始 |
| 4 | [private_import.py:217](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L217) | 弱相关：检查赋值是否访问私有名称，有无value判断但不区分实例属性初始化。 | — |
| 5 | [variables.py:2549](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/variables.py#L2549) | 弱相关：判断函数内变量的注解上下文，不是实例属性初始化。 | — |
| 6 | [typing.py:247](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/typing.py#L247) | 相关：AnnAssign 专属 visitor，处理冗余 Optional/Union 注解；支持分派但不是实例属性主线。 | — |
| 7 | [code_style.py:132](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/code_style.py#L132) | 弱相关：模块字典与 Final 的样式建议，AnnAssign/Assign只是条件之一。 | 片段从行中间开始 |
| 8 | [typecheck.py:1216](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L1216) | 弱相关：普通 Assign 的 visitor，未展示注解与实例属性的区别。 | — |
| 9 | [typecheck.py:525](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L525) | 核心：Assign.targets 与 AnnAssign.target 的归一化；直接支持题目机制。 | — |
| 10 | [inspector.py:220](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/pyreverse/inspector.py#L220) | 相关：实例属性类型收集调用 infer_node，可继续追踪到 get_annotation。 | — |
| 11 | [class_checker.py:1726](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1726) | 相关：实例属性 target 的统一入口，可继续追踪 slots；片段本身未区分注解。 | — |
| 12 | [class_checker.py:762](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L762) | 相关：匹配片段包含 AnnAssign 且 value is None 的 slots 例外，支持声明与初始化区别。 | 片段从行中间开始 |
| 13 | [redefined_variable_type.py:92](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/redefined_variable_type.py#L92) | 弱相关：普通赋值类型重定义检查，无 AnnAssign 对照。 | — |
| 14 | [dataclass_checker.py:58](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/dataclass_checker.py#L58) | 相关：dataclass field 要求 AnnAssign 父节点，属于相似节点类型检查，不是本题主线。 | 片段从行中间开始 |
| 15 | [used_before_assignment_type_annotations.py:93](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/u/used/used_before_assignment_type_annotations.py#L93) | 弱相关：局部变量先使用后赋值的注解测试，不是实例属性。 | — |
| 16 | [exceptions.py:36](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/exceptions.py#L36) | 无关：annotated 指推断结果附带节点，不是 Python 类型注解。 | — |
| 17 | [private_import.py:134](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L134) | 相关：私有名称检查也按 AnnAssign/Assign 分支，可作类比；默认预览未显示该分支。 | 片段从行中间开始 |
| 18 | [inspector.py:370](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/pyreverse/inspector.py#L370) | 无关：从文件构建 Project，未处理实例属性注解。 | — |

</details>

<details>
<summary>666323c9d1d01026 · 1 次调用 · 12 个返回项</summary>

Run：ekGjuDz

原题的首次宽检索，寻找按 AST 节点类型区分属性初始化的机制。

```json
{
  "root": "/app",
  "query": "type-annotated vs non-annotated instance attribute initialization in __init__ AST node separation AnnAssign Assign",
  "limit": 12
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [utils.py:1759](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1759) | 相关：按 AnnAssign 父节点识别指定注解，能证明节点分支；不直接说明实例属性或 no-member。 | — |
| 2 | [private_import.py:21](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L21) | 弱相关：片段是私有导入注解名称解析及赋值访问，AST词汇重合但对象不同。 | 片段从行中间开始 |
| 3 | [variables.py:2549](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/variables.py#L2549) | 弱相关：判断函数内变量的注解上下文，不是实例属性初始化。 | — |
| 4 | [typecheck.py:1216](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L1216) | 弱相关：普通 Assign 的 visitor，未展示注解与实例属性的区别。 | — |
| 5 | [checker.py:396](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/name_checker/checker.py#L396) | 相关：命名检查含 Assign/AnnAssign 分支；默认预览只到405，分支从408后开始。 | — |
| 6 | [class_checker.py:1726](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1726) | 相关：实例属性 target 的统一入口，可继续追踪 slots；片段本身未区分注解。 | — |
| 7 | [bad.py:13](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/doc/data/messages/n/non-parent-init-called/bad.py#L13) | 无关：父构造函数调用告警例子；__init__ 名称匹配不能回答当前问题。 | — |
| 8 | [unused_private_member.py:134](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/u/unused/unused_private_member.py#L134) | 无关：普通 __init__ 私有属性赋值，未提供注解区别。 | — |
| 9 | [used_before_assignment_type_annotations.py:93](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/u/used/used_before_assignment_type_annotations.py#L93) | 弱相关：局部变量先使用后赋值的注解测试，不是实例属性。 | — |
| 10 | [assigning_non_slot.py:35](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/a/assigning/assigning_non_slot.py#L35) | 弱相关：普通属性赋值的 slots 例子，无注解对照。 | — |
| 11 | [good.py:10](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/doc/data/messages/b/bad-super-call/good.py#L10) | 无关：super 调用例子，未涉及属性注解。 | — |
| 12 | [utils.py:1731](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1731) | 核心：注解识别与基类递归；默认预览缺少 1741 的 AnnAssign 条件及后续递归。 | 缺关键 AnnAssign 行 |

</details>

<details>
<summary>d71618a4012b2e8b · 2 次调用 · 10 个返回项</summary>

Run：dZRYmKH、PFvP9Af

原题的首次宽检索，寻找按 AST 节点类型区分属性初始化的机制。

```json
{
  "root": "/app",
  "query": "type-annotated vs non-annotated instance attribute initialization AST node type separation AnnAssign Assign",
  "limit": 10
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [utils.py:1759](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1759) | 相关：按 AnnAssign 父节点识别指定注解，能证明节点分支；不直接说明实例属性或 no-member。 | — |
| 2 | [private_import.py:21](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L21) | 弱相关：片段是私有导入注解名称解析及赋值访问，AST词汇重合但对象不同。 | 片段从行中间开始 |
| 3 | [variables.py:2549](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/variables.py#L2549) | 弱相关：判断函数内变量的注解上下文，不是实例属性初始化。 | — |
| 4 | [checker.py:396](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/name_checker/checker.py#L396) | 相关：命名检查含 Assign/AnnAssign 分支；默认预览只到405，分支从408后开始。 | — |
| 5 | [typecheck.py:1216](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L1216) | 弱相关：普通 Assign 的 visitor，未展示注解与实例属性的区别。 | — |
| 6 | [inspector.py:220](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/pyreverse/inspector.py#L220) | 相关：实例属性类型收集调用 infer_node，可继续追踪到 get_annotation。 | — |
| 7 | [class_checker.py:1726](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1726) | 相关：实例属性 target 的统一入口，可继续追踪 slots；片段本身未区分注解。 | — |
| 8 | [redefined_variable_type.py:92](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/redefined_variable_type.py#L92) | 弱相关：普通赋值类型重定义检查，无 AnnAssign 对照。 | — |
| 9 | [used_before_assignment_type_annotations.py:93](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/u/used/used_before_assignment_type_annotations.py#L93) | 弱相关：局部变量先使用后赋值的注解测试，不是实例属性。 | — |
| 10 | [private_import.py:134](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L134) | 相关：私有名称检查也按 AnnAssign/Assign 分支，可作类比；默认预览未显示该分支。 | — |

</details>

<details>
<summary>867c6ea614f724d8 · 1 次调用 · 8 个返回项</summary>

Run：dZRYmKH

追踪 outside-init 的 AnnAssign/AssignAttr 处理；窄化查询，相关 checker/测试可用。

```json
{
  "limit": 8,
  "query": "checker handles instance attribute defined outside init separately for AnnAssign and AssignAttr node types attribute-defined-outside-init",
  "root": "/app"
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [class_checker.py:1192](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1192) | 相关：outside-init 检查入口及 instance_attrs 遍历，不直接按注解分支。 | — |
| 2 | [typecheck.py:548](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L548) | 相关：枚举属性收集调用 _get_all_attribute_assignments，是可继续追踪的调用方。 | — |
| 3 | [attribute_defined_outside_init.txt:1](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/a/attribute_defined_outside_init.txt#L1) | 相关：outside-init 诊断基准输出，只能验证该告警存在。 | — |
| 4 | [mixin_class_rgx.py:33](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/m/mixin_class_rgx.py#L33) | 相关：mixin 配置与 outside-init 正例，无注解对照。 | — |
| 5 | [access_to_protected_members.py:195](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/a/access/access_to_protected_members.py#L195) | 相关：outside-init 正例，无注解对照。 | — |
| 6 | [class_checker.py:1046](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1046) | 相关：leave_classdef 是 outside-init 检查调用入口，可继续追踪。 | — |
| 7 | [inspector.py:220](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/pyreverse/inspector.py#L220) | 相关：实例属性类型收集调用 infer_node，可继续追踪到 get_annotation。 | — |
| 8 | [utils.py:1731](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1731) | 核心：注解识别与基类递归；默认预览缺少 1741 的 AnnAssign 条件及后续递归。 | 缺关键 AnnAssign 行 |

</details>

<details>
<summary>cb3a08d07b730a79 · 1 次调用 · 8 个返回项</summary>

Run：PFvP9Af

追踪 outside-init 检查与注解/属性 target 的关系；不要求同时返回所有原题证据。

```json
{
  "limit": 8,
  "query": "instance attribute defined outside init check annotated assignment self.attr AnnAssign AssignAttr visitor separation",
  "root": "/app"
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [class_checker.py:1192](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1192) | 相关：outside-init 检查入口及 instance_attrs 遍历，不直接按注解分支。 | — |
| 2 | [typecheck.py:1049](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L1049) | 弱相关：AssignAttr 仅针对 AugAssign 转发，不是 AnnAssign/Assign 区分。 | — |
| 3 | [utils.py:1731](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1731) | 核心：注解识别与基类递归；默认预览缺少 1741 的 AnnAssign 条件及后续递归。 | 缺关键 AnnAssign 行 |
| 4 | [private_import.py:217](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L217) | 弱相关：检查赋值是否访问私有名称，有无value判断但不区分实例属性初始化。 | — |
| 5 | [checker.py:396](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/name_checker/checker.py#L396) | 相关：命名检查含 Assign/AnnAssign 分支；默认预览只到405，分支从408后开始。 | — |
| 6 | [access_to_protected_members.py:195](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/a/access/access_to_protected_members.py#L195) | 相关：outside-init 正例，无注解对照。 | — |
| 7 | [inspector.py:220](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/pyreverse/inspector.py#L220) | 相关：实例属性类型收集调用 infer_node，可继续追踪到 get_annotation。 | — |
| 8 | [mixin_class_rgx.py:36](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/m/mixin_class_rgx.py#L36) | 相关：outside-init 正例，无注解对照。 | — |

</details>

<details>
<summary>1e759e0cc5a06879 · 1 次调用 · 10 个返回项</summary>

Run：pCUARhw

原题的首次宽检索，寻找按 AST 节点类型区分属性初始化的机制。

```json
{
  "root": "/app",
  "query": "type-annotated vs non-annotated instance attribute initialization AST node separation AnnAssign Assign self attribute",
  "limit": 10
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [utils.py:1731](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1731) | 核心：注解识别与基类递归；默认预览缺少 1741 的 AnnAssign 条件及后续递归。 | 缺关键 AnnAssign 行 |
| 2 | [private_import.py:21](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L21) | 弱相关：片段是私有导入注解名称解析及赋值访问，AST词汇重合但对象不同。 | 片段从行中间开始 |
| 3 | [typecheck.py:1049](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L1049) | 弱相关：AssignAttr 仅针对 AugAssign 转发，不是 AnnAssign/Assign 区分。 | — |
| 4 | [class_checker.py:1840](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1840) | 弱相关：普通赋值中的方法参数与属性检查，没有两种赋值节点对照。 | — |
| 5 | [variables.py:2549](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/variables.py#L2549) | 弱相关：判断函数内变量的注解上下文，不是实例属性初始化。 | — |
| 6 | [checker.py:396](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/name_checker/checker.py#L396) | 相关：命名检查含 Assign/AnnAssign 分支；默认预览只到405，分支从408后开始。 | — |
| 7 | [deprecated.py:91](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/deprecated.py#L91) | 无关：弃用属性检查 visitor，不能解释注解赋值区别。 | — |
| 8 | [utils.py:1759](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1759) | 相关：按 AnnAssign 父节点识别指定注解，能证明节点分支；不直接说明实例属性或 no-member。 | — |
| 9 | [used_before_assignment_type_annotations.py:93](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/u/used/used_before_assignment_type_annotations.py#L93) | 弱相关：局部变量先使用后赋值的注解测试，不是实例属性。 | — |
| 10 | [private_import.py:217](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L217) | 弱相关：检查赋值是否访问私有名称，有无value判断但不区分实例属性初始化。 | — |

</details>

<details>
<summary>e1e657bba1b5c72e · 1 次调用 · 8 个返回项</summary>

Run：pCUARhw

追踪类体注解与 outside-init 的关系；正确返回检查入口，不等于证明存在独立 visit_annassign。

```json
{
  "limit": 8,
  "query": "instance attribute defined outside init annotated assignment class body attribute-defined-outside-init visit_annassign",
  "root": "/app"
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [class_checker.py:1192](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1192) | 相关：outside-init 检查入口及 instance_attrs 遍历，不直接按注解分支。 | — |
| 2 | [typecheck.py:548](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L548) | 相关：枚举属性收集调用 _get_all_attribute_assignments，是可继续追踪的调用方。 | — |
| 3 | [attribute_defined_outside_init.txt:1](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/a/attribute_defined_outside_init.txt#L1) | 相关：outside-init 诊断基准输出，只能验证该告警存在。 | — |
| 4 | [mixin_class_rgx.py:33](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/m/mixin_class_rgx.py#L33) | 相关：mixin 配置与 outside-init 正例，无注解对照。 | — |
| 5 | [attribute_defined_outside_init.py:23](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/a/attribute_defined_outside_init.py#L23) | 相关：outside-init 最小例子，未区分注解。 | — |
| 6 | [access_to_protected_members.py:195](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/a/access/access_to_protected_members.py#L195) | 相关：outside-init 正例，无注解对照。 | — |
| 7 | [private_import.py:217](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L217) | 弱相关：检查赋值是否访问私有名称，有无value判断但不区分实例属性初始化。 | — |
| 8 | [class_checker.py:1046](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1046) | 相关：leave_classdef 是 outside-init 检查调用入口，可继续追踪。 | — |

</details>

<details>
<summary>b562c4bd37f5e5e5 · 1 次调用 · 10 个返回项</summary>

Run：TGaFQep

已读 slots 与 Pyreverse 后，追踪声明无值和实例属性收集；带 __init__ 的宽查询返回多项无关初始化例子。

```json
{
  "limit": 10,
  "preview": "full",
  "query": "annotated assignment without value is a declaration not an initialization for instance attributes in class body; separate handling of AnnAssign vs Assign when collecting attributes defined in __init__",
  "root": "/app"
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [typecheck.py:548](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L548) | 相关：枚举属性收集调用 _get_all_attribute_assignments，是可继续追踪的调用方。 | — |
| 2 | [class_checker.py:1141](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1141) | 弱相关：未使用私有属性检查，匹配片段只有 cls/self 的访问关系。 | 片段从行中间开始 |
| 3 | [access_to_protected_members.py:195](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/a/access/access_to_protected_members.py#L195) | 相关：outside-init 正例，无注解对照。 | — |
| 4 | [private_import.py:217](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L217) | 弱相关：检查赋值是否访问私有名称，有无value判断但不区分实例属性初始化。 | — |
| 5 | [bad.py:13](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/doc/data/messages/n/non-parent-init-called/bad.py#L13) | 无关：父构造函数调用告警例子；__init__ 名称匹配不能回答当前问题。 | — |
| 6 | [assigning_non_slot.py:35](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/a/assigning/assigning_non_slot.py#L35) | 弱相关：普通属性赋值的 slots 例子，无注解对照。 | — |
| 7 | [good.py:10](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/doc/data/messages/b/bad-super-call/good.py#L10) | 无关：super 调用例子，未涉及属性注解。 | — |
| 8 | [features.rst:44](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/doc/user_guide/checkers/features.rst#L44) | 无关：多种基础告警列表，未解释当前注解属性机制。 | — |
| 9 | [1.4.rst:322](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/doc/whatsnew/1/1.4.rst#L322) | 弱相关：历史版本多项变更，含注解/属性关键词，不解释当前实现。 | — |
| 10 | [unused_private_member.py:134](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/u/unused/unused_private_member.py#L134) | 无关：普通 __init__ 私有属性赋值，未提供注解区别。 | — |

</details>

<details>
<summary>827adfa8796c5fd7 · 1 次调用 · 15 个返回项</summary>

Run：2BzvctA

原题的首次宽检索，寻找按 AST 节点类型区分属性初始化的机制。

```json
{
  "root": "/app",
  "query": "type-annotated vs non-annotated instance attribute initialization AST node separation AnnAssign Assign",
  "limit": 15
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [utils.py:1759](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1759) | 相关：按 AnnAssign 父节点识别指定注解，能证明节点分支；不直接说明实例属性或 no-member。 | — |
| 2 | [private_import.py:21](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L21) | 弱相关：片段是私有导入注解名称解析及赋值访问，AST词汇重合但对象不同。 | 片段从行中间开始 |
| 3 | [typecheck.py:1216](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L1216) | 弱相关：普通 Assign 的 visitor，未展示注解与实例属性的区别。 | — |
| 4 | [variables.py:2549](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/variables.py#L2549) | 弱相关：判断函数内变量的注解上下文，不是实例属性初始化。 | — |
| 5 | [checker.py:396](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/name_checker/checker.py#L396) | 相关：命名检查含 Assign/AnnAssign 分支；默认预览只到405，分支从408后开始。 | — |
| 6 | [class_checker.py:1840](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1840) | 弱相关：普通赋值中的方法参数与属性检查，没有两种赋值节点对照。 | — |
| 7 | [used_before_assignment_type_annotations.py:93](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/functional/u/used/used_before_assignment_type_annotations.py#L93) | 弱相关：局部变量先使用后赋值的注解测试，不是实例属性。 | — |
| 8 | [inspector.py:220](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/pyreverse/inspector.py#L220) | 相关：实例属性类型收集调用 infer_node，可继续追踪到 get_annotation。 | — |
| 9 | [typing.py:247](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/typing.py#L247) | 相关：AnnAssign 专属 visitor，处理冗余 Optional/Union 注解；支持分派但不是实例属性主线。 | — |
| 10 | [code_style.py:19](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/code_style.py#L19) | 弱相关：大类片段涉及字典转换为 dataclass/NamedTuple，非实例属性初始化。 | 片段从行中间开始 |
| 11 | [private_import.py:134](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L134) | 相关：私有名称检查也按 AnnAssign/Assign 分支，可作类比；默认预览未显示该分支。 | — |
| 12 | [test_utils.py:42](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/pyreverse/test_utils.py#L42) | 相关：get_annotation 的 AnnAssign 测试，提供继续定位函数的线索。 | — |
| 13 | [redefined_variable_type.py:92](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/redefined_variable_type.py#L92) | 弱相关：普通赋值类型重定义检查，无 AnnAssign 对照。 | — |
| 14 | [utils.py:1731](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1731) | 核心：注解识别与基类递归；默认预览缺少 1741 的 AnnAssign 条件及后续递归。 | 缺关键 AnnAssign 行 |
| 15 | [checker.py:157](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/name_checker/checker.py#L157) | 弱相关：大类命中片段位于参数命名与下一个 visitor 的交界，不能直接回答题目。 | 片段从行中间开始 |

</details>

<details>
<summary>ea3a970daff34ec9 · 1 次调用 · 10 个返回项</summary>

Run：NJUKjor

原题的首次宽检索，寻找按 AST 节点类型区分属性初始化的机制。

```json
{
  "root": "/app",
  "query": "type-annotated instance attribute initialization AST node separation AnnAssign vs Assign",
  "limit": 10
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [typecheck.py:1216](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L1216) | 弱相关：普通 Assign 的 visitor，未展示注解与实例属性的区别。 | — |
| 2 | [checker.py:396](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/name_checker/checker.py#L396) | 相关：命名检查含 Assign/AnnAssign 分支；默认预览只到405，分支从408后开始。 | — |
| 3 | [utils.py:1759](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1759) | 相关：按 AnnAssign 父节点识别指定注解，能证明节点分支；不直接说明实例属性或 no-member。 | — |
| 4 | [variables.py:2549](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/variables.py#L2549) | 弱相关：判断函数内变量的注解上下文，不是实例属性初始化。 | — |
| 5 | [private_import.py:21](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L21) | 弱相关：片段是私有导入注解名称解析及赋值访问，AST词汇重合但对象不同。 | 片段从行中间开始 |
| 6 | [class_checker.py:1840](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1840) | 弱相关：普通赋值中的方法参数与属性检查，没有两种赋值节点对照。 | — |
| 7 | [inspector.py:220](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/pyreverse/inspector.py#L220) | 相关：实例属性类型收集调用 infer_node，可继续追踪到 get_annotation。 | — |
| 8 | [redefined_variable_type.py:92](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/redefined_variable_type.py#L92) | 弱相关：普通赋值类型重定义检查，无 AnnAssign 对照。 | — |
| 9 | [code_style.py:19](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/code_style.py#L19) | 弱相关：大类片段涉及字典转换为 dataclass/NamedTuple，非实例属性初始化。 | 片段从行中间开始 |
| 10 | [basic_checker.py:960](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/basic_checker.py#L960) | 弱相关：普通赋值自赋值与重复名称检查，缺少注解和实例属性语义。 | — |

</details>

<details>
<summary>cd8e2309f3bfabb7 · 1 次调用 · 8 个返回项</summary>

Run：NJUKjor

已读 class_checker 等实现后，寻找 AnnAssign/Assign 实例属性处理差异；top1 归一化函数直接回答。

```json
{
  "limit": 8,
  "query": "instance attribute defined with annotation in __init__ treated differently from plain assignment; separate handling of AnnAssign target AssignAttr vs Assign",
  "root": "/app"
}
```

| # | 固定版本源码 | 相关性与理由 | 呈现 |
| ---: | --- | --- | --- |
| 1 | [typecheck.py:525](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/typecheck.py#L525) | 核心：Assign.targets 与 AnnAssign.target 的归一化；直接支持题目机制。 | — |
| 2 | [private_import.py:217](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/extensions/private_import.py#L217) | 弱相关：检查赋值是否访问私有名称，有无value判断但不区分实例属性初始化。 | — |
| 3 | [refactoring_checker.py:1771](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/refactoring/refactoring_checker.py#L1771) | 无关：字符串 join 重构中的 AugAssign，与实例属性注解无关。 | — |
| 4 | [checker.py:396](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/base/name_checker/checker.py#L396) | 相关：命名检查含 Assign/AnnAssign 分支；默认预览只到405，分支从408后开始。 | — |
| 5 | [variables.py:2549](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/variables.py#L2549) | 弱相关：判断函数内变量的注解上下文，不是实例属性初始化。 | — |
| 6 | [utils.py:1731](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/utils.py#L1731) | 核心：注解识别与基类递归；默认预览缺少 1741 的 AnnAssign 条件及后续递归。 | 缺关键 AnnAssign 行 |
| 7 | [class_checker.py:1840](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/pylint/checkers/classes/class_checker.py#L1840) | 弱相关：普通赋值中的方法参数与属性检查，没有两种赋值节点对照。 | — |
| 8 | [test_utils.py:61](https://github.com/pylint-dev/pylint/blob/44740e5e5cdd2b80321a4f0ed4254213d058b9f6/tests/pyreverse/test_utils.py#L61) | 相关：从 __init__ 参数注解恢复 self.x 类型的测试；当前片段未展示测试函数调用。 | — |

</details>

