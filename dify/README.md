# 读心酒馆 · 老周的第九个问题

> OceanBase 2026 数据库大赛 · AI 生态赛题 · Dify《Just for Fun》参赛作品

城南小酒馆,酒保老周**不会读心,只是记性好**。你在心里想一个东西,他只问"是/不是",
一步步把它逼出来。而你玩过的每一局——你偏哪一类、你怎么答话、他第几问中的——
都被他记在 **PowerContext** 的账本上。下次你换个会话、换个浏览器推门进来,只要还是"你",
他就直接翻旧账,并且**开局就问对问题**。

一句话玩法:**AI 读心 + 跨会话记忆,而且记忆的好处能量化。**

## 30 秒看懂它好不好玩

| 现象 | 为什么成立 |
| --- | --- |
| 生面孔:"头一回见,规矩是这样的…" | 召回状态 `empty`,老周不装熟 |
| 第二次见面:"你上回连开三局都想吃的" | 召回状态 `ready`,账本被注入到提示词里 |
| 实测同一玩家:第 1 局 10 问 → 第 2、3 局各 6 问 | 老周跳过了账本里已知的方向,先验更准 |
| 结算时回一句"📔 入账 1 条" | 那是 `/v1/memory/remember` 的真实回执 |
| 猜错后他下次自己念出自嘲 | 观察与结果一起写进了记忆 |

技术分工:**Dify 承担编排与局内状态,PowerContext 承担跨会话的玩家记忆。**
Dify 会话变量活不出一个会话,所以"下次还认得你"这件事只能由 PowerContext 做——
这不是装饰性依赖,是玩法成立的前提(论证见 `docs/01-作品设计.md` 第 5 节)。

## 目录

```
dify/
├── README.md                        本文件
├── app/
│   └── mindtavern_chatflow.yml      Dify Chatflow DSL(导入它就行)
├── docs/
│   ├── 01-作品设计.md               创意、玩法、记忆契约、为什么必须 PowerContext
│   ├── 02-运行说明.md               环境/版本/依赖/导入/配置/入口/限制
│   └── 03-验证样例.md               三组可按步骤复现的验收样例与证据
├── scripts/
│   ├── run_powercontext.ps1         一键装 + 起 PowerContext Server(Windows,无 Docker)
│   ├── smoke_test_powercontext.py   记忆链路自检(建区/写/召回/隔离)
│   └── dump_memory.py               把某玩家的记忆全查出来,可当审计证据
├── tools/
│   ├── validate_dsl.py              DSL 结构校验:引用/连线/分支句柄/可达性
│   └── dsl_replay.py                按 DSL 逐节点回放的执行器(没有 Dify 也能完整玩)
└── evidence/                        运行日志、记忆快照、界面截图
```

## 最短上手路径

```powershell
# 1) 起记忆服务(不需要 Docker,SQLite 后端)
cd dify
.\scripts\run_powercontext.ps1                 # 令牌写在 dify\PC_TOKEN.txt

# 2) 自检记忆链路
set PC_BASE_URL=http://127.0.0.1:8100
set PC_TOKEN=<上一步打印的令牌>
python scripts\smoke_test_powercontext.py

# 3) Dify 里导入 app/mindtavern_chatflow.yml,
#    在应用"环境变量"里填 PC_BASE_URL / PC_TOKEN,在 LLM 节点选一个已配置的模型,
#    发布后用网页或 API 对话。(自建 Dify 容器内访问宿主机: PC_BASE_URL=http://host.docker.internal:8100)

# 4) 想看证据
python scripts\dump_memory.py --user zhang-03 --format md --out evidence\memory-zhang-03.md
```

没有 Dify 环境也能完整体验玩法(逐节点执行 DSL 本体,不是另一套实现):

```bash
export LLM_API_KEY=<你的 OpenAI 兼容密钥>
python tools/dsl_replay.py --user zhang-03 --conversation s1 --say 开局 --trace
python tools/dsl_replay.py --autolive rounds=4 --user zhang-03 --out evidence/run-logs/autolive
```

详细说明见 `docs/02-运行说明.md`,验收步骤见 `docs/03-验证样例.md`。

## 与赛题要求的对应

| 赛题要求 | 本作品 |
| --- | --- |
| 必须使用 Dify 并交付 DSL | Chatflow(advanced-chat)DSL:`app/mindtavern_chatflow.yml`,23 节点 / 24 连线 |
| 至少实际使用 seekdb 或 PowerContext | PowerContext:建区 / 写记忆 / 召回注入,三处都在功能主链路上,掉线即降级为"没带账本" |
| 应用能运行、可按样例体验主要功能 | `docs/03-验证样例.md` 三组样例:跨会话记忆、每局问题数曲线、玩家隔离 |
| 指定技术实际发挥作用 | 有 `ready` 与 `empty` 两种开场、有可量化的"问题数下降",可从 Dify 运行日志与 `dump_memory.py` 双向核对 |
| 材料足以复现 | 启动脚本、自检脚本、DSL 校验器、无 Dify 也能跑的回放器;配置示例全部用占位值,不含任何真实密钥 |

## 许可与来源说明

- 本目录内的 DSL、脚本、文档与文案均为本次参赛原创。
- PowerContext 调用契约取自 `oceanbase/powercontext` 的公开 HTTP API(`/v1/scopes`、`/v1/context/prepare`、
  `/v1/memory/remember`、`/v1/memory/entries/list`)。
- 未复制任何第三方 Dify 工作流 DSL;`docs/01` 里提到的公开示例仅用于玩法启发,未使用其代码。
