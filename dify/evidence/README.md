# 运行证据说明

本目录是《读心酒馆》的验收证据,全部由脚本自动产出,可用 `docs/03-验证样例.md` 的步骤重放。

| 文件 | 内容 | 怎么产生的 |
| --- | --- | --- |
| `run-logs/dify-curve4.json` / `.md` | **真实 Dify 实例内**连打 4 局的逐轮实录(每轮玩家输入、老周回复、会话 ID、耗时、token 用量) | `scripts/run_dify_evidence.py --user zhang-04 --rounds 4 --words 苹果,锅贴,米饭,生菜` |
| `run-logs/dify-final.json` / `.md` | 另一组跨类别对局(同一脚本,`--user zhang-03`) | 同上 |
| `run-logs/dify-xiaow.*` | 另一个玩家身份的对照局(验证记忆不串号) | 同上,`--user xiaow` |
| `run-logs/dify-run-pre-fix.*` | 早期版本留下的实录,暴露出"问题数被连续 open 归零"的缺陷;当前 DSL 已修 | 同上 |
| `memory/zhang-04.md`、`memory/zhang-03.md` | 该玩家在 PowerContext 里的**全部记忆**(scope_id、条数、每条 kind/状态/正文)+ 注入给模型的上下文包 | `scripts/dump_memory.py --user zhang-03 --format md` |
| `memory/xiaow-empty.md` | 新玩家:0 条记忆、召回状态 `empty` —— 与上面形成分岔对照 | 同上 |
| `powercontext-access.log` | 服务端操作日志:`create_scope` / `prepare_context` / `remember_memory` 的 operation、outcome、status_code、耗时 | PowerContext Server 自带 access log |

## 三条最关键的技术证据

1. **记忆确实被写入并可审计。**
   `memory/zhang-03.md` 里每条 `round-record` 都是 Dify 的 `写入记忆` 节点
   (`POST /v1/memory/remember`)真实产生的,格式为
   `读心酒馆|战绩|第N局|玩家=…|类别=…|答案=…|问题数=…|命中/未中|观察=…`。
2. **记忆确实改变了模型行为。**
   `run-logs/dify-final.md` 中,同一玩家换新会话后,老周的开场会直接引用上一局的具体旧事
   (第几局、什么类别、几个问题、中没中),并按账本调整第一问;而 `xiaow` 的开场是生面孔口径。
3. **两组数据都保留,不做挑选。**
   同类重复选择会让判别变难(5→8→9→12),跨类别选择则明显省问题(10→6→6);
   我们同时提交两组,并在 `docs/03` 说明差异来源,不宣称"问题数单调下降"。

## 与 Dify 运行界面对照

评审如需界面证据:在 Dify 应用 **日志** 页打开任一"结算轮"的运行详情,
可以看到 `取玩家的 scope`、`翻账本`、`写入记忆` 三个 HTTP 节点的请求与响应,
与本目录的 JSON/日志逐条对应(同一 `scope_id`、同一记忆正文)。
