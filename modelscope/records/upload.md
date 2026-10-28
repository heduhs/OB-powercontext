# 上传过程记录

时间：2026-10-28 19:29（UTC+8）｜ 魔搭账号：**heduhs**

## 1. 工具与凭证

```bash
pip install -U modelscope          # 1.40.2（附带 modelscope_hub 0.5.1，提供 ms CLI）
ms --version                       # ModelScope Hub CLI
export MODELSCOPE_API_TOKEN=<魔搭访问令牌，未写入任何提交物>
ms whoami                          # username: heduhs
```

令牌来源：魔搭网页「账号设置 → 访问令牌」里已有的 `default`（写权限、长期有效）。
本机受控浏览器登录后由 `ms` 直接读取使用，**没有**写入仓库、脚本或记忆。

## 2. 打包

```bash
cd modelscope/seekdb-retrieval-eval
zip -r ../seekdb-retrieval-eval.zip . -x '*__pycache__*'
```

自检结果（`ms create --skill-file` 的硬性要求）：

| 约束 | 实测 |
| --- | --- |
| 包体 ≤ 5 MB | 86.8 KB |
| 根目录恰好一个 `SKILL.md` | `['SKILL.md']`，全库仅此一个 |
| `SKILL.md` front-matter 含 `name` / `version` / `description` | `seekdb-retrieval-eval` / `1.0.0` / 314 字 |
| 文件数 | 47 |
| 敏感信息扫描（本地路径、令牌前缀） | 无命中 |

## 3. 创建并发布

```bash
ms create heduhs/seekdb-retrieval-eval \
  --repo-type skill \
  --skill-file modelscope/seekdb-retrieval-eval.zip \
  --category developer-tools \
  --visibility public \
  --license apache-2.0 \
  --chinese-name "seekdb 检索质量评测" \
  --description "在 seekdb 上量化评测检索效果：……"
```

输出：

```
Uploading skill file: modelscope\seekdb-retrieval-eval.zip
Created skill: heduhs/seekdb-retrieval-eval
```

CLI 内部走的是魔搭 OpenAPI：`POST /files/upload`（拿到 file id）→ `POST /skills`（`{owner, skill_name, display_name, description, license, category, private:false, skill_file:<fileId>}`）。
完整命令输出见同目录 `upload_cli.log`，接口回读见 `openapi_skill_detail.json`。

## 4. 上传后校验

| 校验 | 命令/方式 | 结果 |
| --- | --- | --- |
| 元数据公开可读（无 Token） | `GET /openapi/v1/skills/heduhs/seekdb-retrieval-eval` | `success: true`，`private: False`，`category: developer-tools`，`license: apache-2.0` |
| 匿名下载回读 | `HubApi().download_skill("heduhs/seekdb-retrieval-eval")`（清空环境变量后执行） | 解出 **47** 个文件，`SKILL.md` 12145 字节，front-matter 首行 `name: seekdb-retrieval-eval` |
| 网页可访问 | 浏览器打开 Skill 详情页 | 标题、简介、`Skill 介绍`（SKILL.md 渲染）、`Skill 文件`（master 分支 `Init Skill` 提交，含 assets/records/references/scripts）均正常 |
| 安装命令下发 | 详情接口 `install_command` | `modelscope skills add heduhs/seekdb-retrieval-eval` 等三种 |

截图见 `screenshots/`。

## 5. 追加验证行并重新发布（平台弹窗的强制步骤）

提测弹窗「获取验证行」返回本队验证行：

```
<!-- OB-S:kA4XusNzrex2ZMHHwEpG6g -->
```

要求"原样、独立一行添加到 SKILL.md 正文末尾，再到魔搭社区发布公开的 Skill"。
魔搭 Skill 底层是 git 仓库，因此直接改文件推送，网址不变：

```bash
printf '\n<!-- OB-S:kA4XusNzrex2ZMHHwEpG6g -->\n' >> SKILL.md
git clone https://www.modelscope.cn/skills/heduhs/seekdb-retrieval-eval.git
git commit -am "docs: 追加大赛作品验证行到 SKILL.md 正文末尾"
git push "https://oauth2:${MODELSCOPE_API_TOKEN}@www.modelscope.cn/skills/heduhs/seekdb-retrieval-eval.git" master
# remote: new_oid=7e2b2fcb5da289e0b7c7f8cbbe95c15a782c1550  result=SKIPPED(not_targeted) → 推送成功
```

复核（不带任何凭证）：

```bash
curl -s https://www.modelscope.cn/skills/heduhs/seekdb-retrieval-eval/resolve/master/SKILL.md | tail -3
# → <!-- OB-S:kA4XusNzrex2ZMHHwEpG6g -->
```

## 6. 提测与结果

| 项 | 内容 |
| --- | --- |
| 提测入口 | https://open.oceanbase.com/train/detail/21?questionId=600075 （ModelScope 页签 → 立即提测） |
| 报名仓库地址 | `https://github.com/heduhs/OB-seekdb#ob-seekdb`（平台锁定，与 powercontext 题用的是不同仓库） |
| 作品链接 | `https://modelscope.cn/skills/heduhs/seekdb-retrieval-eval`（无查询参数、无锚点） |
| 提交时间 | 2026-10-28 19:35 |
| 结果 | **执行成功，成绩 20.000 / 20**，提测次数 1，成功 1 / 失败 0 |

## 7. 结果

| 项 | 值 |
| --- | --- |
| Skill 网址 | https://modelscope.cn/skills/heduhs/seekdb-retrieval-eval |
| Skill 编号（标识） | `heduhs/seekdb-retrieval-eval` |
| 内部 `_id` | `5Q6QPrmrzYh22e0fqaGd1g==` |
| 可见性 | public |
| 版本日期 | 2026.10.28 |

> 说明：`search=seekdb-retrieval-eval` 的列表接口暂时只回显了另一位作者的同号 Skill（`guda1019/...`），属于搜索索引延迟；直接按 `owner/name` 取详情与匿名下载均已通过，判定以详情页为准。
