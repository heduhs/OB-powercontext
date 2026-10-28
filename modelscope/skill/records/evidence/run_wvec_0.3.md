| strategy | recall@1 | recall@10 | precision@10 | MRR | nDCG@10 | empty_result_rate | avg_latency_ms |
|---|---|---|---|---|---|---|---|
| vector | 0.4583 | 0.8021 | 0.1438 | 0.8234 | 0.7477 | 0.0000 | 2.3 |
| fulltext | 0.5417 | 0.8542 | 0.1500 | 0.8854 | 0.8100 | 0.0000 | 2.3 |
| hybrid_weighted | 0.5417 | 0.9167 | 0.1625 | 0.8875 | 0.8393 | 0.0000 | 2.3 |

queries=16 depth=50 table=demo_kb seconds=0.12

## 诊断
- 混合检索最优，相对最好单腿 +0.0625 recall@10。
- 当前 backend=local（离线自检后端），指标只用于验证链路，不要当作检索质量结论。
