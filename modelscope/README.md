# 魔搭社区赛题 · 上传并分享自己的 Skill

| 项目 | 说明 |
| --- | --- |
| 赛题 | 魔搭社区 —— 上传并分享自己的 Skill |
| 任务 | 将自己编写的一个 Skill 上传到魔搭社区 Skills 中心 |
| 提交产物 | 该 Skill 在魔搭社区的网址 |
| 总分 | 20 |
| 我们的 Skill | **seekdb-retrieval-eval**（seekdb 检索质量评测） |
| 魔搭网址 | https://modelscope.cn/skills/heduhs/seekdb-retrieval-eval |
| 状态 | 已上传并通过页面可访问性校验 |

## 一、为什么选这个 Skill

赛题只要求"上传一个自己写的 Skill"，但一个只有提示词模板的 Skill 无法证明它真的能用。
我们把本次大赛两条产品线（**seekdb** 的向量/全文混合检索、**AI 生态**的 Agent Skill）在同一个作品里连起来：

> **seekdb-retrieval-eval** —— 给一份语料和一组带标注的查询，在 seekdb 上量化跑出向量召回、全文召回、RRF 混合、加权混合四种策略的 recall@k / MRR / nDCG@10，扫描融合权重与候选池深度，并按症状输出根因诊断。

它是可执行的评测工具而不是语法说明书：Skill 里的每一条 SQL、每一个数字都在本机 seekdb v1.3.0.0 上真机跑过，`records/evidence/` 随包携带原始报告。

## 二、作品构成

```
modelscope/
├── README.md                      本文件
├── ONE_PAGER.md                   技术一页纸
├── VALIDATION.md                  验收对照 + 提测结果
├── skill/                         上传到魔搭的完整 Skill（与 zip 内容一致）
│   ├── SKILL.md                   Agent 使用的技能正文（含诊断表与实测 SQL）
│   ├── README.md                  人读的安装与使用说明
│   ├── scripts/                   db.py / embed.py / build_index.py / evaluate.py / metrics.py
│   │                              + run_evidence.sh（一键复现全部证据）
│   ├── references/                sql-patterns.md / diagnostics.md / metrics-and-tuning.md
│   ├── assets/sample/             36 篇语料 + 16 条标注查询，开箱可跑
│   └── records/evidence/          真机实测的 report.json/md 与 SUMMARY.md
└── records/
    ├── seekdb-retrieval-eval.zip  实际上传到魔搭的包（86 KB，根目录恰好一个 SKILL.md）
    ├── upload.md                  上传命令与返回结果
    └── screenshots/               魔搭页面访问证据
```

## 三、本地复现（3 条命令）

```bash
pip install pymysql
cd skill
python scripts/build_index.py --corpus assets/sample/corpus.jsonl --table demo_kb
python scripts/evaluate.py   --qrels assets/sample/qrels.jsonl   --table demo_kb --out report
bash scripts/run_evidence.sh    # 重跑全部消融实验，产出 records/evidence/*
```

前提是本机有一个 seekdb 实例（`127.0.0.1:2881`，`root`，空密码，`test` 库）：

```bash
# Windows 本机
"C:\Program Files\seekdb\bin\seekdb.exe" --nodaemon --base-dir=<数据目录> \
    --port=2881 --parameter memory_limit=2G --log-level=WARN
# 或 Docker
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb:latest
```

## 四、实测结果（backend=local，36 篇语料 / 16 条标注查询）

| strategy | recall@1 | recall@10 | MRR | nDCG@10 | empty_result_rate |
| --- | --- | --- | --- | --- | --- |
| vector | 0.4583 | 0.8021 | 0.8234 | 0.7477 | 0.0 |
| fulltext | 0.5417 | 0.8542 | 0.8854 | 0.8100 | 0.0 |
| hybrid_rrf | 0.5104 | 0.8854 | 0.8667 | 0.8175 | 0.0 |
| hybrid_weighted | 0.5417 | **0.8958** | 0.8906 | **0.8427** | 0.0 |

三条最有价值的实测结论：

1. **中文全文检索的静默失效可被量化**：全文索引不写 `with parser ik` 时，全文腿 recall@10 从 0.8542 掉到 **0.3021**、空结果率 **0.5625**，而 SQL 不报任何错。
2. **融合权重存在内部最优，且 recall 与 MRR 峰值不重合**：`w_vec=0.3` 时 recall@10 达峰 0.9167（高于两条单腿），MRR 却在 `w_vec=0.7` 达峰 0.9271。
3. **评测口径本身会骗人**：同一个 `--filter category=hybrid`，配 `--only-tag hybrid` 时 recall@10=1.0，不配时只有 0.125——工具会把"过滤后的 recall 上限 17%"直接算出来打在报告里。

## 五、上传过程

见 `records/upload.md`。核心命令（官方 `modelscope` CLI，`ms create --repo-type skill`）：

```bash
pip install -U modelscope
ms login --token "$MODELSCOPE_API_TOKEN"
ms create <owner>/seekdb-retrieval-eval --repo-type skill \
  --skill-file records/seekdb-retrieval-eval.zip \
  --category developer-tools --visibility public \
  --description "<技能简介>"
```
