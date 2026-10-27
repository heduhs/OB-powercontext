# 规有所依 —— 面向软件持续演进的 SDD 上下文管理系统

> MiniMax × OceanBase 赛题作品。为 AI 开发助手提供“任务所需的规格、代码和测试”，
> 并用 MiniMax 完成一次真实代码修改，全程可复现、可验证。

## 1. 系统做什么

围绕**规格驱动开发（SDD）**，本模块根据一个开发任务：

1. 从 seekdb 检索**适用的规格条目、需要修改的代码、用于验证的测试**，形成开发上下文；
2. 把上下文交给使用 **MiniMax-M1** 的开发助手，完成一次真实代码修改；
3. 运行功能测试与回归测试验证修改；
4. 把任务进展与验证结果保存到 **PowerContext**，模块重启后可读回并复用。

演示业务工程为 `demo_project/`（一个带完整测试的订单管理库 `ordersvc`），
验证任务为 **TASK-001：为 `list_orders` 增加分页能力**（见 `tasks/TASK-001.md`）。

## 2. 技术分工（对应赛题“技术结合要求”）

| 技术 | 承担的作用 | 接入方式 | 证据 |
| --- | --- | --- | --- |
| **MiniMax** | 参与本次任务的代码修改（MiniMax-M1）；工程资料的向量嵌入（embo-01） | HTTP API（chatcompletion_v2 / embeddings） | `records/minimax-dev-record-*.json` |
| **seekdb** | 存储规格/代码/测试的索引，混合检索结果用于构造任务上下文 | MySQL 协议 + pyseekdb 客户端 | `records/db-query-record.json` |
| **PowerContext** | 保存任务进展与验证结果，重启后读取并注入后续上下文 | HTTP API + Python Client | `records/powercontext-state-record.json` |

## 3. 依赖与版本

- Python 3.11+（本机验证于 3.12）
- seekdb 服务端 v1.3.0（Windows MSI，MySQL 协议，监听 `127.0.0.1:2881`）
- PowerContext 1.4.1（`pip install "powercontext[cli,server]==1.4.1"`）
- Python 包：`pyseekdb`、`powercontext`、`pymysql`、`pytest`、`opentelemetry-api`、
  `opentelemetry-sdk`、`pydantic-settings`
- MiniMax API Key（大赛赠送的 Token Plan）

```bash
pip install pyseekdb pymysql pytest powercontext==1.4.1 \
    opentelemetry-api opentelemetry-sdk pydantic-settings
```

## 4. 配置

全部通过环境变量配置，**不含任何真实凭据**：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MINIMAX_API_KEY` | （空） | MiniMax API Key，必填 |
| `MINIMAX_BASE_URL` | `https://api.minimaxi.com/v1` | MiniMax 接口地址 |
| `MINIMAX_MODEL` | `MiniMax-M1` | 代码修改使用的模型 |
| `SDD_SEEKDB_HOST/PORT` | `127.0.0.1` / `2881` | seekdb 地址 |
| `SDD_SEEKDB_USER` | `root` | seekdb 用户（配合 tenant） |
| `SDD_SEEKDB_TENANT` | `sys` | seekdb 租户 |
| `SDD_SEEKDB_PASSWORD` | （空） | seekdb 密码 |
| `SDD_SEEKDB_DATABASE` | `sddctx` | seekdb 库名 |
| `POWERCONTEXT_URL` | `http://127.0.0.1:8000` | PowerContext 服务地址 |
| `SDD_EMBEDDING_MODE` | `auto` | `auto`/`minimax`/`local`；auto 在有 Key 时用 MiniMax 嵌入，否则回退本地 ONNX |

## 5. 启动依赖服务

```bash
# 5.1 seekdb（Windows 服务，安装后）
"C:\Program Files\seekdb\bin\seekdb_manage.ps1" start
# 建库
python -c "import pymysql; pymysql.connect(host='127.0.0.1',port=2881,user='root@sys',password='',autocommit=True).cursor().execute('CREATE DATABASE IF NOT EXISTS sddctx')"

# 5.2 PowerContext（基础记忆模式，SQLite，无需模型 API）
powercontext config init --template -o .env      # 首次生成配置
powercontext server run --env-file .env          # 保持运行
```

## 6. 数据初始化与调用入口

模块入口为 CLI（`python -m sddctx <command>`），输入/输出见下：

```bash
export MINIMAX_API_KEY=你的key

# 6.1 导入规格/代码/测试到 seekdb（输出：导入数量、按类型统计、嵌入provider）
python -m sddctx import

# 6.2 为任务构造上下文（输出：适用规格条目、需修改代码、验证测试、已保存状态）
python -m sddctx context --task "为 list_orders 增加分页参数 page 和 page_size" --top-k 5

# 6.3 完整流程：上下文 -> MiniMax 改代码 -> 跑测试 -> 保存状态到 PowerContext
python -m sddctx run --task-id TASK-001 --task "$(cat tasks/TASK-001.md)"

# 6.4 读取已保存任务状态（重启后仍可读取）
python -m sddctx state --task-id TASK-001

# 6.5 存储统计
python -m sddctx stats
```

## 7. 一键端到端验证

```bash
python validate_demo.py            # 完整流程（需 MINIMAX_API_KEY）
python validate_demo.py --dry-run  # 除 MiniMax 调用外的全部步骤
```

`validate_demo.py` 覆盖赛题全部必做项，逐步打印 PASS/FAIL，并把结果写入
`records/validation-log-*.json`。最近一次运行 **7/7 全部 PASS**（见该文件）。

## 8. 测试命令

```bash
cd demo_project
python -m pytest tests/test_orders_pagination.py -v   # 目标功能测试（TASK-001）
python -m pytest tests/test_orders_regression.py -v   # 回归测试（既有行为）
```

## 9. 目录结构

```
minimax/
├── sddctx/                 # 上下文模块（本赛题交付的核心代码）
│   ├── cli.py              #   调用入口（import/context/run/state/stats）
│   ├── store.py            #   seekdb 解析、导入与混合检索
│   ├── embeddings.py       #   MiniMax embo-01 嵌入 + 本地 ONNX 回退
│   ├── state.py            #   PowerContext 任务状态保存/读回
│   ├── minimax_editor.py   #   MiniMax-M1 代码修改 + 测试验证 + 记录
│   └── config.py
├── demo_project/           # 演示业务工程（可运行、带测试）
│   ├── ordersvc/           #   被修改的业务代码
│   ├── specs/              #   规格（SPEC-001 查询、SPEC-002 状态机）
│   ├── spec_updates/       #   规格演进项（v1.2 加分页、v1.3 加页大小上限）
│   └── tests/              #   回归测试 + 目标功能测试
├── tasks/TASK-001.md       # 验证任务定义（要改什么 / 哪些行为必须保持）
├── validate_demo.py        # 端到端验证脚本
├── records/                # 技术使用记录（MiniMax/数据库/PowerContext/测试/diff）
└── snapshots/              # 修改前后代码快照（before/after）
```

## 10. 已知限制

- MiniMax-M1 为推理模型，单次代码修改约 10–60s；已内置重试。
- 演示工程为内存态订单库，聚焦“上下文→修改→验证→记忆”闭环，不含持久化业务数据。
- 嵌入 `auto` 模式在无 Key 时回退本地 ONNX（all-MiniLM-L6-v2），中文检索质量低于 embo-01。
