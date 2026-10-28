| strategy | recall@1 | recall@10 | precision@10 | MRR | nDCG@10 | empty_result_rate | avg_latency_ms |
|---|---|---|---|---|---|---|---|
| vector | 0.4583 | 0.8021 | 0.1438 | 0.8234 | 0.7477 | 0.0000 | 1.7 |
| fulltext | 0.1250 | 0.3021 | 0.0500 | 0.2812 | 0.2489 | 0.5625 | 1.7 |
| hybrid_rrf | 0.5104 | 0.8646 | 0.1500 | 0.8750 | 0.7991 | 0.0000 | 1.7 |

queries=16 depth=50 table=kb_no_ik seconds=0.09

## 诊断
- 混合检索最优，相对最好单腿 +0.0625 recall@10。
- 当前 backend=local（离线自检后端），指标只用于验证链路，不要当作检索质量结论。
