# 验收对照

赛题：魔搭社区 —— 上传并分享自己的 Skill（20 分）
完整流程要求：**准备自己的 Skill → 上传到魔搭社区 → 获取网址或编号 → 提交到测试平台**

## 一、赛题「提交前检查」逐条对照

| 检查项 | 赛题要求 | 我们的证据 | 结论 |
| --- | --- | --- | --- |
| Skill 上传 | 已将自己的 Skill 成功上传到魔搭社区 | `records/upload_cli.log`（`Created skill: heduhs/seekdb-retrieval-eval`）；`records/openapi_skill_detail.json`（`private: false`）；`records/screenshots/` | 达成 |
| 信息有效 | 网址或编号能准确定位到该 Skill，并可查看上传内容 | 网址 https://modelscope.cn/skills/heduhs/seekdb-retrieval-eval ；**清空凭证后**匿名 `download_skill()` 回读成功，47 个文件、`SKILL.md` 12145 字节完整 | 达成 |
| 平台提交 | 已在测试平台的魔搭社区赛题入口提交网址或编号 | 提测记录：执行成功，成绩 **20.000/20**（2026-10-28 19:35，首次提测） | 达成 |
| 验证行（平台弹窗新增要求） | 本队验证行原样、独立一行写入 `SKILL.md` 正文末尾后重新发布 | 末行 `<!-- OB-S:kA4XusNzrex2ZMHHwEpG6g -->`；匿名 `download_skill()` 与 `resolve/master/SKILL.md` 均回读到该行（魔搭提交 `7e2b2fc`） | 达成 |

## 二、赛题 2.1 步骤对照

| 赛题步骤 | 落实情况 |
| --- | --- |
| 1. 注册并登录魔搭社区账号 | 账号 `heduhs`（`ms whoami` 校验通过） |
| 2. 准备一个自己编写的 Skill，按魔搭流程完成上传 | 自研 `seekdb-retrieval-eval`（SKILL.md + 5 个可执行脚本 + 3 篇参考文档 + 示例语料/标注 + 真机实测证据），按官方 `ms create --repo-type skill --skill-file` 流程上传 |
| 3. 上传成功后确认能查看内容，并获取网址或编号 | 详情页「Skill 介绍 / Skill 文件」均可查看，编号 `heduhs/seekdb-retrieval-eval` |
| 建议：清晰名称与功能说明 + 简短使用示例 | 名称 `seekdb-retrieval-eval`（中文显示名「seekdb 检索质量评测」）；`description` 314 字写明"做什么 + 何时用 + 触发词"；SKILL.md 内含「30 秒跑通」两条命令与真实输出、诊断表、SQL 示例 |
| 不要填个人主页 / 账号 ID / 仅填名称 | 提交的是 Skill 详情页完整链接，非个人主页、非纯名称 |

## 三、Skill 自身的功能验证（seekdb v1.3.0.0 真机）

运行环境：Windows 10 x64，`seekdb.exe --port=2881 --parameter memory_limit=2G --log-level=WARN`，Python 3.12 + PyMySQL。
原始输出全部在 `skill/records/evidence/`（随包上传到魔搭，见「Skill 文件」页），汇总见 `skill/records/evidence/SUMMARY.md`。

| 验证点 | 结果 |
| --- | --- |
| 建库：单条 DDL 同时建 HNSW 向量索引 + ik 中文全文索引 | 36 篇语料写入，`show index` 回读 `PRIMARY/BTREE`、`ftidx/FULLTEXT`、`vidx/VECTOR` |
| 四策略评测跑通 | vector / fulltext / hybrid_rrf / hybrid_weighted 全部产出指标，最优 hybrid_weighted recall@10 = 0.8958 |
| 诊断规则可被真实触发 | 中文分词器消融：全文腿 recall@10 0.8542 → 0.3021、空结果率 0 → 0.5625，工具打出"全文腿空结果"诊断 |
| 参数扫描可用 | `--w-vec` 0.0/0.3/0.5/0.7/1.0 曲线（recall 峰 0.9167@0.3，MRR 峰 0.9271@0.7）；`--depth` 10/20/50/100（小语料无信号，已如实记录） |
| 过滤上限诊断 | `--filter category=hybrid` 未配 `--only-tag` 时打出"过滤后 recall 上限 17%"，指标 1.0000 vs 0.1250 |
| 索引确实被使用 | `explain` 出现 `VECTOR INDEX SCAN demo_kb(vidx)`、`TEXT RETRIEVAL SCAN demo_kb(ftidx)` + `calc_relevance=true` |
| 单条 SQL 混合检索（RRF） | CTE + `row_number()` + `union all` + `group by` 在 v1.3.0.0 直接执行成功 |
| 错误路径与预检 | 表不存在 / 缺 FULLTEXT / 缺 VECTOR / `depth<k` / 未知策略 / 非法过滤列 / 非法标识符 / 缺 embedding 环境变量 / doc_id 重复 —— 逐条人工触发，均为可读提示而非堆栈 |
| 安全 | 表名与分词器走标识符白名单（`'a;drop'`、`'ik) x'` 被拦截），其余全部参数化绑定；包内无密钥、无本机绝对路径 |
| 一键复现 | `bash scripts/run_evidence.sh` 全量重跑通过（exit=0），三次重跑数字完全一致 |

## 四、提测结果

| 项 | 内容 |
| --- | --- |
| 提测赛题 | ModelScope（魔搭 Skill 发布，20 分） · questionId=600075 |
| 报名仓库地址 | https://github.com/heduhs/OB-seekdb#ob-seekdb（平台锁定，不可改） |
| 作品验证行 | `<!-- OB-S:kA4XusNzrex2ZMHHwEpG6g -->`（本队共用，已原样独立一行追加到 `SKILL.md` 正文末尾） |
| 作品链接 | https://modelscope.cn/skills/heduhs/seekdb-retrieval-eval |
| 提交时间 | 2026-10-28 19:35 |
| 判题结果 | **执行成功 · 成绩 20.000 / 20**（最高成绩 20.000，最新成绩 20.000，提测次数 1，成功 1 / 失败 0） |

截图：`records/screenshots/test_record_20.png`（提测记录页）、`skill_page_top.png` / `skill_page_files.png`（魔搭详情页）。

## 五、给后来者的流程提醒（语雀文档未覆盖）

语雀《赛题》页只写了"上传 → 拿网址 → 提测"，但**平台的实际提测弹窗多一步强制校验**：

1. 先在「立即提测」弹窗点 **获取验证行**（此时不用填作品链接）；
2. 把验证行**原样、独立一行**追加到 `SKILL.md` 正文末尾，再重新发布公开 Skill；
3. 然后把作品页面地址填进「作品链接」提交，链接不要带查询参数或锚点；
4. 平台自动校验三件事：作品公开可访问 + `SKILL.md` 有效 + 含本队验证行。同一作品只能被一队认领。

重新发布的方式：魔搭 Skill 本质是 git 仓库，`git clone https://www.modelscope.cn/skills/<owner>/<name>.git` 后改 `SKILL.md` 再 push（用 `oauth2:<token>@` 形式的鉴权地址）即可，比重新上传 zip 更省事，也不会换掉网址。

