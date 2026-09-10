# Query-conditioned retrieval replay

完成评分 240 / 240 次；独立 QA 任务 1 道。质量统一使用预定第 1 次，五次只检验固定请求的检索重复性。

| unit | query 类型 | 目标排名 | 原任务依赖入口排名 | 到目标字节 | 4 KiB 目标排名 | 相同输出 / 5 次 |
|---|---|---:|---:|---:|---:|---|
| original-fts | original | 未命中 | 未命中 | — | 未命中 | True (5/5 已评分) |
| original-vector | original | 6 | 6 | 3270 | 6 | True (5/5 已评分) |
| original-hybrid | original | 6 | 6 | 4513 | 未命中 | True (5/5 已评分) |
| query-11bd3e44a0473bda-fts | equivalent_rewrite | 2 | 2 | 1354 | 2 | True (5/5 已评分) |
| query-11bd3e44a0473bda-vector | equivalent_rewrite | 未命中 | 未命中 | — | 未命中 | True (5/5 已评分) |
| query-11bd3e44a0473bda-hybrid | equivalent_rewrite | 3 | 3 | 2042 | 3 | True (5/5 已评分) |
| query-4490ab82ae7eb077-fts | equivalent_rewrite | 4 | 4 | 2214 | 4 | True (5/5 已评分) |
| query-4490ab82ae7eb077-vector | equivalent_rewrite | 8 | 8 | 4592 | 未命中 | True (5/5 已评分) |
| query-4490ab82ae7eb077-hybrid | equivalent_rewrite | 未命中 | 未命中 | — | 未命中 | True (5/5 已评分) |
| query-56d56c4841c181f4-fts | legitimate_subgoal | 2 | 未命中 | 679 | 2 | True (5/5 已评分) |
| query-56d56c4841c181f4-vector | legitimate_subgoal | 未命中 | 未命中 | — | 未命中 | True (5/5 已评分) |
| query-56d56c4841c181f4-hybrid | legitimate_subgoal | 4 | 未命中 | 2409 | 4 | True (5/5 已评分) |
| query-647fe4ecb6a2ae1d-fts | equivalent_rewrite | 4 | 4 | 2214 | 4 | True (5/5 已评分) |
| query-647fe4ecb6a2ae1d-vector | equivalent_rewrite | 9 | 9 | 5635 | 未命中 | True (5/5 已评分) |
| query-647fe4ecb6a2ae1d-hybrid | equivalent_rewrite | 9 | 9 | 5366 | 未命中 | True (5/5 已评分) |
| query-6f7818ee47659dc5-fts | equivalent_rewrite | 10 | 10 | 5942 | 未命中 | True (5/5 已评分) |
| query-6f7818ee47659dc5-vector | equivalent_rewrite | 4 | 4 | 2223 | 4 | True (5/5 已评分) |
| query-6f7818ee47659dc5-hybrid | equivalent_rewrite | 5 | 5 | 3332 | 5 | True (5/5 已评分) |
| query-7d806974be2d61b0-fts | equivalent_rewrite | 2 | 2 | 1354 | 2 | True (5/5 已评分) |
| query-7d806974be2d61b0-vector | equivalent_rewrite | 未命中 | 未命中 | — | 未命中 | True (5/5 已评分) |
| query-7d806974be2d61b0-hybrid | equivalent_rewrite | 5 | 5 | 3486 | 5 | True (5/5 已评分) |
| query-b5c43bc240a1b4ad-fts | ambiguous | unknown | 未命中 | — | unknown | True (5/5 已评分) |
| query-b5c43bc240a1b4ad-vector | ambiguous | unknown | 未命中 | — | unknown | True (5/5 已评分) |
| query-b5c43bc240a1b4ad-hybrid | ambiguous | unknown | 未命中 | — | unknown | True (5/5 已评分) |
| query-cb5cdcce70db0237-fts | legitimate_subgoal | 3 | 未命中 | 1689 | 3 | True (5/5 已评分) |
| query-cb5cdcce70db0237-vector | legitimate_subgoal | 8 | 未命中 | 4057 | 8 | True (5/5 已评分) |
| query-cb5cdcce70db0237-hybrid | legitimate_subgoal | 5 | 未命中 | 2768 | 5 | True (5/5 已评分) |
| query-ce364241d8efcba0-fts | legitimate_subgoal | 未命中 | 10 | — | 未命中 | True (5/5 已评分) |
| query-ce364241d8efcba0-vector | legitimate_subgoal | 4 | 4 | 2223 | 4 | True (5/5 已评分) |
| query-ce364241d8efcba0-hybrid | legitimate_subgoal | 9 | 5 | 6024 | 未命中 | True (5/5 已评分) |
| query-e27c4cf0a2491e88-fts | equivalent_rewrite | 8 | 8 | 4648 | 8 | True (5/5 已评分) |
| query-e27c4cf0a2491e88-vector | equivalent_rewrite | 3 | 3 | 1921 | 3 | True (5/5 已评分) |
| query-e27c4cf0a2491e88-hybrid | equivalent_rewrite | 1 | 1 | 748 | 1 | True (5/5 已评分) |
| query-ef0fa9295718e8ed-fts | legitimate_subgoal | 1 | 未命中 | 630 | 1 | True (5/5 已评分) |
| query-ef0fa9295718e8ed-vector | legitimate_subgoal | 10 | 未命中 | 5695 | 未命中 | True (5/5 已评分) |
| query-ef0fa9295718e8ed-hybrid | legitimate_subgoal | 2 | 未命中 | 1103 | 2 | True (5/5 已评分) |
| faithful-02da5dd906d31bd54297 | legitimate_subgoal | 4 | 未命中 | 2409 | 4 | True (5/5 已评分) |
| faithful-2e26e889035df597618e | equivalent_rewrite | 13 | 13 | 8770 | 未命中 | True (5/5 已评分) |
| faithful-7495d0e842963fc41e12 | legitimate_subgoal | 9 | 5 | 6024 | 未命中 | True (5/5 已评分) |
| faithful-83640d8664f44fce898b | equivalent_rewrite | 5 | 5 | 3332 | 5 | True (5/5 已评分) |
| faithful-8c2461eaae4af1398627 | equivalent_rewrite | 1 | 1 | 1190 | 1 | True (5/5 已评分) |
| faithful-8e91ddbe45cd0c2ef6fe | equivalent_rewrite | 5 | 5 | 3332 | 5 | True (5/5 已评分) |
| faithful-bb45130df287f3bbc5a7 | equivalent_rewrite | 5 | 5 | 3332 | 5 | True (5/5 已评分) |
| faithful-bf538455b0531d77031e | equivalent_rewrite | 9 | 9 | 6004 | 未命中 | True (5/5 已评分) |
| faithful-bfec359ff4ff454f7356 | equivalent_rewrite | 6 | 6 | 4010 | 6 | True (5/5 已评分) |
| faithful-d20aa5018e5f10a81f2c | equivalent_rewrite | 6 | 6 | 3702 | 6 | True (5/5 已评分) |
| faithful-d51e59c19e96a7504de0 | equivalent_rewrite | 5 | 5 | 3332 | 5 | True (5/5 已评分) |
| faithful-da01fcfd78c1b726ef9e | equivalent_rewrite | 6 | 6 | 3501 | 6 | True (5/5 已评分) |

受控视图固定 limit=10，逐条文本运行 FTS / vector / hybrid。faithful 视图保持实际请求及省略的默认参数，完整 request 的开发标注为主列；附加分文本分数仍基于联合输出，不构成 route 归因。

query 对应文本与实际来源见 replay-plan.json。JSON 同时保留 Hit@1/5/10、RR@10、bridge、原任务入口、4/8 KiB 预算视图、原始哈希和全部失败。字节是输出窗口代理，不是模型 input token。不同改写来自同题，不能算成 12 道独立题。
