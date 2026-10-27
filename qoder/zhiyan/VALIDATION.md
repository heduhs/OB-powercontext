# 验证样例（Validation Sample）

按以下步骤可完整体验智研的主要功能，并核对 seekdb / PowerContext / MiniMax 的实际作用。
前置：已按 `README.md` 第 6 节启动 seekdb、PowerContext、后端，并设置 `MINIMAX_API_KEY`。

## 样例 A：混合检索（seekdb 实际参与）

1. 打开 `http://127.0.0.1:8010`（实时模式徽标应显示“实时模式 · seekdb+MiniMax”）。
2. 在「智能检索」输入 `向量数据库如何做混合检索`，点击「检索」。
3. **预期**：返回 Top-K 结果，首条为《数据库课程笔记 · 第11讲 向量数据库》或
   《实验记录：向量检索性能对比》，带相关度分数与出处（标题/章节/标签）。
4. 切换类型过滤为「文献」，输入 `HNSW 算法`。
   **预期**：仅返回 type=paper 的结果，首条为 HNSW 论文。
5. 核对证据：`records/db-query-record.json`（真实 seekdb 查询与命中）。

## 样例 B：基于资料的问答（MiniMax 实际参与）

1. 切到「AI 问答」，输入 `RAG 是如何减少大模型幻觉的？`，点击「提问」。
2. **预期**：约数秒后返回 grounded 答案，正文含 `[编号]` 标注，下方列出引用出处
   （应包含《Retrieval-Augmented Generation ...》）。
3. 核对证据：`records/minimax-record.json`。

## 样例 C：薄弱点记忆 + 间隔重复（PowerContext 实际参与）

1. 切到「薄弱知识点」，输入 `MVCC 如何实现可重复读隔离`，来源填 `数据库课程笔记 第7讲`，点击「标记薄弱点」。
2. **预期**：列表出现该薄弱点，`下次复习` 为今天，状态「待复习」。
3. 点击评分按钮 `5`（记得很牢）。
   **预期**：该点 `复习次数` +1，`下次复习` 推到约 1 天后，从「今日复习」消失。
4. 再标记一个点并评 `1`（完全忘了）。
   **预期**：间隔重置为 1 天，仍出现在「今日复习」或次日计划。
5. **重启复用验证**：重启后端进程（`Ctrl+C` 后重新 `python -m backend.app`），
   刷新页面，切到「薄弱知识点」。
   **预期**：之前标记的薄弱点与复习进度**仍在**（PowerContext 持久化，跨进程复用）。
6. 核对证据：`records/powercontext-record.json`（scope、Memory 条目、复习计划）。

## 样例 D：演示模式（发布站点形态）

1. 停止本地后端，仅用任意静态服务器托管 `frontend/`（或访问已发布的 Qoder Sites 站点）。
2. **预期**：徽标显示「演示模式 · 示例数据」；检索/薄弱点/复习使用内置示例数据与
   localStorage，交互正常且明确标注为演示；问答为示例拼接并注明“实时模式由 MiniMax-M1 生成”。

## 重置方法

```bash
# 清空 seekdb 语料集合
python -c "import pyseekdb; pyseekdb.RemoteServerClient(host='127.0.0.1',port=2881,tenant='sys',user='root',password='',database='zhiyan').delete_collection('zhiyan_corpus')"
# 重新导入
python -m backend.ingest
# 清空演示模式本地记忆（浏览器）：清除 localStorage 键 zhiyan_weak_points_v1
```

> 说明：检索/问答结果依赖 MiniMax 嵌入与模型，网络波动时接口内置重试；
> 若 MiniMax 不可用，检索仍可用（需先以 embo-01 建库），问答会明确提示服务不可用。
