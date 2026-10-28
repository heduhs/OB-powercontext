# records · 实测证据索引

| 文件 | 内容 | 生成方式 |
| --- | --- | --- |
| `assets-manifest.json` | 每个模型/运行时文件的字节数与 sha256（体积口径与压缩比的原始依据） | `npm run setup` |
| `bench-configs.json` | 4 个「精度 × 后端」配置的完整评测：环境、体积、分阶段计时、Recall/nDCG/MRR、逐查询排名、RRF 12 组参数扫描、seekdb 侧 SQL | 页面 `/eval.html` 或 `npm run bench` |
| `bench-configs-run1.json` | 同上的前一轮运行（用于说明 HNSW 近似检索与端侧吞吐的运行间波动） | 同上 |
| `bench-ablation.json` | 优化项消融：批大小 1/4/16、WASM 线程 1/8、索引刷新“每批 vs 末尾一次” | `/eval.html` 勾选消融 |
| `run-smoke.json` | 主流程端到端记录（后端徽标、引擎信息、导入指标、检索指标、seekdb SQL 证据、结果列表） | `npm run smoke` |
| `run-degrade.json` | 强制 WASM 的降级路径端到端记录 | `npm run degrade` |
| `screenshots/smoke-app.png` | WebGPU + fp16 下的应用界面（导入与检索结果） | `npm run smoke` |
| `screenshots/degrade-app.png` | WASM + q8 降级路径下的应用界面 | `npm run degrade` |
| `screenshots/eval-report.png` | 评测台报告页 | `npm run bench` |

数据均为本机实测原始输出，未做手工修饰；`bench-*.json` 里的 `environment` 字段记录了设备、浏览器、
`crossOriginIsolated`、WebGPU adapter、seekdb 版本、SDK 版本、语料规模与切片参数，可据此复核。
