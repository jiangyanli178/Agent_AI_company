# 策略体检所 StrategyClinic

> 一家由 1 个人 + 6 个 AI 员工运营的量化策略工作室。
> 你说人话，AI 帮你找策略、跑回测、把结果翻译成人话。
>
> 别的工具回答「怎么用」，我们回答「**该不该用**」。

JUNCTION CHINA 2026 · OPC 一人公司 · Xiaomi MiMo 赛道

---

## 怎么启动（Windows）

**双击 `0_一键启动.bat`** ——只需要这一个。

它会检查端口：后端没跑就开一个后端窗口，前端没跑就开一个前端窗口，
等前端起来后自动打开浏览器（http://localhost:5173）。
**已经在跑的时候再双击它，不会再起第二份，只会打开浏览器。**

| 想干什么 | 双击哪个 |
|---|---|
| 启动（日常就这一个） | `0_一键启动.bat` |
| 停止全部 | `0_停止.bat` |
| 后端卡住了 | `restart_backend.bat` |
| 只改后端代码、想自动重启 | `start_backend_dev.bat` |

其余几个是内部用的，平时不用管：

| 文件 | 作用 |
|---|---|
| `start_backend.bat` | 只启动后端（8000），单进程 |
| `start_frontend.bat` | 只启动前端（5173） |
| `restart_backend.bat` | 先杀干净再启动后端 |
| `port_lock.bat` | 启动锁，被上面几个脚本 `call` 调用，**不要单独双击** |

> 这些 bat 是 **GBK + CRLF** 编码（中文 Windows 的 cmd 要求）。
> 用 VSCode 打开可能显示乱码，这不影响运行；但**别用编辑器另存成 UTF-8**，
> 否则中文行会被 cmd 从中间切断，报"不是内部或外部命令"。

---

## 目录结构

```
jragent/
├── DEVELOPMENT.md          ← 开发顺序清单，照着做（先读这个）
├── 项目总说明.md            ← 多 Agent 总说明（含 MiMo 智能体与工作室调度关系）
├── backend/                FastAPI + SQLAlchemy + MySQL
│   ├── schema.sql          15 表 + 2 视图 + 种子数据
│   ├── app/
│   │   ├── services/       核心服务（回测/积分/工具调用）
│   │   └── routers/        API 路由
│   ├── opc_quant_studio/   量化研究工作室引擎（COO + 11 Agent + 9 道闸门）
│   ├── scripts/            拉数、信号、对账、周报、备份
│   ├── migrations/         数据库迁移（只新增，不改旧文件）
│   └── tests/              pytest
├── frontend/               Vue3 + Vite + Pinia + ECharts（两个前端同站合并）
│   └── public/studio/      量化工作室页面（http://localhost:5173/studio/ 访问，API 走 /api/studio/*）
├── studio/                 量化研究工作室 · 运行数据（API 在 backend/app/routers/studio.py）
│   ├── demo.py             全流程演示入口（CHARTER 宪章被 API 复用）
│   ├── context_hub/        工作室运行卷宗（含 orders/payments、07-log 留痕）
│   └── outputs/            回测交付物（report.md + 图表 PNG）
├── agents/                 MiMo 独立智能体（各自端口 / 登录态隔离）
│   ├── mimo-social-agent/  刷视频社交互动 + 每日策略回撤流水线
│   └── mimo-zxg-agent/     腾讯自选股运营（汇总 → 校验 → 发帖）
├── hub/                    Context Hub · 公司大脑（9 卷宗，文档版）
├── docs/                   设计文档（含 agent-architecture 11 Agent 架构详版）
└── deploy/                 Nginx / systemd 配置
```

> **两套卷宗别弄混**：`hub/` 是「策略体检所」的公司大脑文档卷宗（人写的经营文档）；
> `studio/context_hub/` 是「量化研究工作室」的**运行态**卷宗（COO 自动写入留痕、订单与支付记录），
> 两者互不覆盖。

---

## 已就绪的模块（已自测通过）

| 文件 | 内容 |
|---|---|
| `backend/app/services/backtest.py` | 5 种策略、8 个指标、防未来函数、涨跌停拦截 |
| `backend/app/services/credits.py` | 积分账本：行锁扣费、幂等、按量计费、失败退回 |
| `backend/app/services/agent_tools.py` | 6 个工具 + 系统提示词 + 执行器 |
| `backend/scripts/fetch_bars.py` | 前复权拉数、增量更新、脏数据清洗 |
| `backend/schema.sql` | 建库脚本，已通过 MySQL 语法校验 |
| `hub/00-charter.md` | 公司宪章（含立案决策记录） |
| `hub/04-ops.md` | SOP、9 道人工闸门、合规禁词表 |
| `hub/06-finance.md` | 单位经济、对账 SQL、周报模板 |

---

## 快速开始

### 1. 准备环境

```bash
# 先建 .gitignore 再 git init（顺序反了 .env 可能被跟踪）
git init

cd backend
python -m venv .venv
.venv/Scripts/activate                                    # Windows
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
cp .env.example .env                                       # 然后填数据库密码
```

### 2. 建库

```bash
# 先确认 MySQL 已启动：netstat -ano | grep 3306
mysql -u root -p < backend/schema.sql
```

### 3. 拉数据

```bash
cd backend
python scripts/fetch_bars.py --dry-run      # 先看看会写什么
python scripts/fetch_bars.py --full         # 首次全量拉 5 年
python scripts/fetch_bars.py                # 之后每天增量
```

### 4. 启动后端

```bash
cd backend
uvicorn app.main:app --reload --port 8000
# 打开 http://127.0.0.1:8000/docs
```

### 5. 启动前端

```bash
cd frontend
npm config set registry https://registry.npmmirror.com
npm install
npm run dev
# 打开 http://localhost:5173
```

### 6. 跑测试

```bash
cd backend && pytest -q
```

---

## 三条不可违反的工程约定

1. **表结构以 `schema.sql` 为唯一真相**，改表只在 `migrations/` 新增文件，不改旧文件
2. **密钥只进 `.env`**，不进 Git、不进 prompt、不进卷宗
3. **回测与部署共用同一个 `run_backtest()`**，AI 只输出「框架 + 参数」，不生成代码

---

## 合规红线

- 不荐股、不预测涨跌、不承诺收益
- 不代客下单、不接触客户资金，所有部署为模拟盘
- 不伪造用户评价与经营数据，没有数据就写「尚未产生」
- 积分不可提现、不可转让、不可兑换现金
- 真实收付款必须老板人工确认（闸门⑥）
