| strategy | recall@1 | recall@10 | precision@10 | MRR | nDCG@10 | empty_result_rate | avg_latency_ms |
|---|---|---|---|---|---|---|---|
| vector | 0.4583 | 0.8021 | 0.1438 | 0.8177 | 0.7477 | 0.0000 | 1.6 |
| fulltext | 0.5417 | 0.8542 | 0.1500 | 0.8854 | 0.8100 | 0.0000 | 1.6 |
| hybrid_rrf | 0.5104 | 0.8958 | 0.1562 | 0.8516 | 0.8083 | 0.0000 | 1.6 |
| hybrid_weighted | 0.5104 | 0.8958 | 0.1562 | 0.8802 | 0.8277 | 0.0000 | 1.6 |

queries=16 depth=10 table=demo_kb seconds=0.11

## 诊断
- 混合检索最优，相对最好单腿 +0.0416 recall@10。
- 当前 backend=local（离线自检后端），指标只用于验证链路，不要当作检索质量结论。
