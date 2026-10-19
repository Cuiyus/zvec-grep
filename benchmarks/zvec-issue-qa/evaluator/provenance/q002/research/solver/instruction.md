请依据提供的源码快照，解释下面的查询行为，并给出文件与行号证据。无需修改代码或运行测试；如做了验证，请区分实际执行结果与源码推断。

集合包含以下三条非空数据，body 有全文索引（standard tokenizer、lowercase filter），status（STRING）和 views（INT64）有倒排索引，使用默认查询配置，没有删除记录：

| id | status | views | body |
| --- | --- | --- | --- |
| 1 | draft | 10 | hello one |
| 2 | published | 20 | hello two |
| 3 | published | 30 | hello three |

所有查询 topk=10。报告的行为如下（比较 ID 集合，不要求结果顺序）：

- 全文查询 hello，同时加 status = 'archived' 或 views > 1000，返回 1、2、3；只做同一标量条件查询则返回空集合。
- 全文查询 hello，加 status = 'published'，返回 2、3。
- 全文查询 one，加 status = 'published'，返回空集合。
- 另建相同数据、仅去掉 status 和 views 倒排索引的集合，保留 body 全文索引；全文查询 hello 加上述零匹配标量条件，返回空集合。

请解释异常和三个对照为何不同，沿相关源码说明条件如何传递和执行；提出能保留正常无条件查询、正常带条件查询行为的最小修复原则，并描述覆盖此问题及边界情况的回归检查。
