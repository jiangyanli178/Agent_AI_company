# 开发顺序 · 一步一步做
## 策略体检所 StrategyClinic · jragent

> 项目根目录：`E:\pycharmxm\jc\jragent`
> **本清单不砍功能**，按完整形态列出。做不完时从最后的「砍单顺序」倒着砍。
> 每一步都写清：做什么、前置依赖、**做完你能看到什么**。

---

## ✅ 环境验证结果（2026-11-06 已实测）

| 项 | 结果 |
|---|---|
| MySQL 8.0.12 连接 | ✅ root/root 可连，3306 正常 |
| `schema.sql` 执行 | ✅ 24 条语句全部成功，17 个对象（15 表 + 2 视图） |
| 种子数据 | ✅ 套餐 4 / 标的 30 / 策略 9 / 知识库 7 |
| FastAPI 骨架 | ✅ `/api/health` 返回 200，`db_connected: true` |
| MiMo API | ✅ key 有效，非流式调用成功 |
| **MiMo 工具调用** | ✅ **直接兼容我们的 `TOOLS` 定义**，模型自主调用了 `search_strategies` 与 `list_symbols` |

### MiMo 实测确认的三个配置细节

```
BASE_URL   https://api.xiaomimimo.com/v1
MODEL      mimo-v2.6-pro            ← 全小写，不是 MiMo-V2.6-Pro
认证头     api-key: sk-xxx           ← 不是 Authorization: Bearer（两种我们都试过，api-key 可用）
参数名     max_completion_tokens     ← 不是 max_tokens
```

工具调用返回结构（标准 OpenAI 兼容）：
```json
{"finish_reason": "tool_calls",
 "message": {"content": "...", "tool_calls": [
   {"id": "call_xxx", "function": {"name": "search_strategies",
                                   "arguments": "{\"difficulty\": 1, \"limit\": 3}"}}],
  "reasoning_content": "..."}}
```

**注意**：响应里带 `reasoning_content`（思考模式默认开启）。
多轮工具调用时，官方建议**保留历史 `reasoning_content`** 以获得最佳表现。

### MySQL 8.0.12 的一个坑（已修）

`CHECK` 约束在 **8.0.16 之前只解析不强制执行**，
所以 `schema.sql` 里的 `ck_balance CHECK (balance >= 0)` 实际上不生效。

已在 `credits.py` 的扣费语句改成**条件 UPDATE** 兜底：
```sql
UPDATE credit_accounts SET balance = balance - :p
WHERE user_id = :u AND balance >= :p     -- 余额不够就一行都不更新
```
即便行锁失效，余额也绝不可能被扣成负数。

---

## ✅ 进度更新（2026-11-06 23:15）

### P0 / P1 / P2 / P3 / P4 已全部完成并端到端验证

**数据**：30 只标的 × 5 年前复权日线，36,337 行，31 秒拉完，0 失败。

**P4 全流程实测结果**（`TestClient` 真实调用）：

```
1. 注册           → user_id=1，送 100 积分 ✅
2. 策略列表       → 9 个策略 ✅
3. 数据截止       → 2026-11-06，30 只，36337 行 ✅
4. 回测           → 扣 10 分，余额 90
                    茅台 3 年：收益 -28.55%，基准 -12.39%，回撤 40.05%
                    净值曲线 728 个点，附人话信号解释 ✅
5. 流水           → GIFT +100 → CONSUME -10，余额一致 ✅
6. 充值 9.9 元    → 到账 100 积分，余额 190
                    重复核销返回 duplicated，不重复入账 ✅
```

**到这里，商业闭环已经跑通**：注册送分 → 回测扣分 → 充值到账 → 幂等防重。

### 下一步：P5 AI 层（产品的灵魂）

只剩 `mimo.py` / `kb.py` / `agent_log.py` / `critic.py` / `context.py` / `routers/ai.py` 六个文件。
其中 `routers/ai.py` 的工具循环是核心，MiMo 的工具调用格式已实测兼容。

---

## 〇、先补的遗漏点（之前几版没提，但开发时一定会撞上）

| # | 遗漏 | 不补的后果 |
|---|---|---|
| 1 | **`.gitignore` 没有建** | `.env` 一进 Git，MiMo key 和数据库密码就泄漏了 |
| 2 | **没有数据库迁移机制** | `schema.sql` 改一个字段，只能删库重灌，数据全没 |
| 3 | **没有统一异常处理** | 积分不足有的接口返回 500 有的返回 200，前端没法拦截 |
| 4 | **合规审查 Agent 只在文档里** | 演示要"故意失败一次"，没有 `critic.py` 就演不出来 |
| 5 | **欢迎邮件没有** | 官方场景二的第一单定义 = 支付 + **欢迎邮件发出**，缺一环 |
| 6 | **没有备份脚本** | 现场数据库挂了就全完 |
| 7 | **时区没统一** | 云服务器默认 UTC，cron 会错 8 小时，信号在凌晨生成 |
| 8 | **自测脚本不是 pytest** | 改动后没法一键回归，改坏了自己不知道 |
| 9 | **数据库连接池没配** | 并发稍高就 `Too many connections` |
| 10 | **Nginx 没关 `proxy_buffering`** | AI 流式输出会整段卡住，最后一次性喷出来 |

---

## 一、总览：开发顺序与依赖

```
P0 仓库环境
   └─→ P1 数据层（建库 + 拉数）
          └─→ P2 后端骨架
                 └─→ P3 核心服务（已写好 3 个，接测试）
                        └─→ P4 业务路由（注册/积分/策略/回测）
                               ├─→ P5 AI 层（工具循环 + 合规审查）
                               └─→ P6 前端
                                      └─→ P7 运营闭环（周报/分享/欢迎/备份）
                                             └─→ P8 卷宗补齐
                                                    └─→ P9 部署演练
```

**关键路径**：P1 → P2 → P4 → P6。
P5 AI 层可以在 P4 之后、与 P6 前端并行做（如果两人分工）。

---

## P0 · 仓库与环境（0.5h）

| 步骤 | 文件 | 做什么 | 验证 |
|---|---|---|---|
| 0.1 | `.gitignore` | 屏蔽 `.env`、`__pycache__`、`node_modules`、`.idea`、备份文件 | ✅ 已生成 |
| 0.2 | `README.md` | 启动说明、目录说明、常用命令 | ✅ 已生成 |
| 0.3 | `backend/.env` | 从 `.env.example` 复制，填真实数据库密码 | 文件存在且**不在 Git 里** |
| 0.4 | 建 venv 装依赖 | `pip install -r requirements.txt -i 阿里源` | `uvicorn --version` 能出 |
| 0.5 | 启动 MySQL | 本机 MySQL 9.4 已装但服务未启动 | `netstat -ano \| grep 3306` 有输出 |
| 0.6 | `git init` + 首次提交 | 版本管理，方便回滚 | `git log` 有一条 |

> ⚠️ 顺序提醒：**先建 `.gitignore` 再 `git init`**，反过来的话 `.env` 可能已经被跟踪。

---

## P1 · 数据层（2h）

| 步骤 | 文件 | 做什么 | 前置 | 验证 |
|---|---|---|---|---|
| 1.1 | `backend/schema.sql` | 执行建库（已写好） | MySQL 启动 | `SHOW TABLES` 出 15 张表 |
| 1.2 | `backend/migrations/001_init.sql` | 把 `schema.sql` 软链或复制为首版迁移 | 1.1 | 有版本记录 |
| 1.3 | `backend/migrations/README.md` | 约定：以后改表只新增 `00X_xxx.sql`，不改旧文件 | — | 团队看懂 |
| 1.4 | `backend/scripts/fetch_bars.py` | 全量拉数（已写好） | 1.1 + 装 akshare | 30 只标的 5 年日线入库 |
| 1.5 | 数据校验 SQL | 检查行数、日期范围、有无 close<=0 | 1.4 | 每只标的约 1200 行 |

```bash
cd backend && python scripts/fetch_bars.py --full          # 首次全量
python scripts/fetch_bars.py --dry-run                     # 先看看会写什么
python scripts/fetch_bars.py --code 600519                 # 只补一只
```

**校验 SQL**：
```sql
SELECT symbol_code, COUNT(*), MIN(trade_date), MAX(trade_date)
FROM daily_bars GROUP BY symbol_code ORDER BY COUNT(*) LIMIT 5;
SELECT COUNT(*) FROM daily_bars WHERE close_price <= 0;   -- 必须为 0
```

---

## P2 · 后端骨架（2h）

按顺序写，每个文件都能单独验证：

| 步骤 | 文件 | 做什么 | 验证 |
|---|---|---|---|
| 2.1 | `app/config.py` | pydantic-settings 读 `.env`，含 DB/JWT/MiMo/时区 | `python -c "from app.config import settings; print(settings.DB_NAME)"` |
| 2.2 | `app/db.py` | SQLAlchemy engine + 连接池（`pool_size=10, max_overflow=20, pool_pre_ping=True`） | 能 `SELECT 1` |
| 2.3 | `app/exceptions.py` | 统一错误格式 + 402 积分不足 + 全局 handler | 见 2.7 |
| 2.4 | `app/logging_config.py` | 日志落 `logs/app.log`，按天轮转 | `logs/` 有文件 |
| 2.5 | `app/security.py` | bcrypt 哈希 + JWT 签发/校验 | 能生成并解出 token |
| 2.6 | `app/deps.py` | `get_current_user`、`get_db`、`require_admin` | 被路由引用 |
| 2.7 | `app/main.py` | FastAPI 实例 + CORS + 注册异常 handler + 注册路由 | `uvicorn app.main:app --reload` 后 `/docs` 能开 |

**`main.py` 里必须有的三行**：
```python
app.add_exception_handler(InsufficientCredits, insufficient_handler)   # 返回 402
app.add_exception_handler(Exception, global_error_handler)             # 统一错误格式
app.add_middleware(CORSMiddleware, allow_origins=[...])                # 开发期放开
```

**时区**：`config.py` 里统一 `TZ = "Asia/Shanghai"`，所有 `datetime.now()` 换成带时区的写法。

---

## P3 · 核心服务（已写好，接测试，1h）

这三个文件**已经迁移完成并自测通过**，这一步是把自测转成正式 pytest：

| 步骤 | 文件 | 状态 | 要做什么 |
|---|---|---|---|
| 3.1 | `app/services/backtest.py` | ✅ 已就绪 | 不动 |
| 3.2 | `app/services/credits.py` | ✅ 已就绪 | 不动 |
| 3.3 | `app/services/agent_tools.py` | ✅ 已就绪 | 不动 |
| 3.4 | `tests/test_backtest.py` | 待写 | 5 种策略 + 防未来函数 + 涨跌停断言 |
| 3.5 | `tests/test_credits.py` | 待写 | 幂等、余额不足、退回、按量结算、账目一致 |
| 3.6 | `tests/conftest.py` | 待写 | SQLite 内存库 fixture |

```bash
cd backend && pytest -q        # 全绿才能往下走
```

> ⚠️ 记住：**SQLite 不支持 `FOR UPDATE`**，并发扣费的验证要单独在 MySQL 上做一次。

---

## P4 · 业务路由（4h）

**严格按这个顺序**，因为后面依赖前面：

| 步骤 | 文件 | 做什么 | 验证（看到什么算完成） |
|---|---|---|---|
| 4.1 | `app/routers/auth.py` | 注册/登录/我，注册时送 100 积分 | 注册后 `balance=100` |
| 4.2 | `app/routers/credits.py` | 余额、套餐、流水、充值、核销 | 充值后余额增加，流水有记录 |
| 4.3 | `app/routers/strategies.py` | 策略列表/详情/分类 | `/docs` 里能查到 9 个策略 |
| 4.4 | `app/routers/symbols.py` | 标的列表/日线 | 能拿到 600519 的 K 线 |
| 4.5 | `app/routers/backtest.py` | 扣分 → 跑回测 → 存 JSON → 返回 | 一次回测扣 10 分并出曲线 |
| 4.6 | `app/routers/deployments.py` | 观察列表（简化版部署） | 能加入/暂停/查看 |

**4.5 的扣费顺序不能错**：
```python
entry = charge(engine, uid, "BACKTEST", job_no)   # 先扣
try:
    result = run_backtest(...)
    save_result(job_no, result)
except Exception:
    refund(..., source_idem=f"bt:{job_no}")       # 失败退
    raise
```

**4.6 降级说明**：只做"加入观察列表"存配置，信号展示用预生成数据。
完整调度放到 P7，来不及就不做。

---

## P5 · AI 层（4h，产品的灵魂）

| 步骤 | 文件 | 做什么 | 验证 |
|---|---|---|---|
| 5.1 | `app/services/mimo.py` | httpx 异步调 MiMo，支持 tools 参数与流式 | 能拿到一次非流式回答 |
| 5.2 | `app/services/kb.py` | MySQL 全文检索 + 关键词兜底 | 查"金叉"能命中 |
| 5.3 | `app/services/agent_log.py` | 写 `agent_runs` 表（含重试次数、成本、错误） | 表里有记录 |
| 5.4 | `app/services/critic.py` | **合规审查 Agent**：禁词扫描 + 打回重写 ≤3 轮 | 输入"稳赚"会被拦 |
| 5.5 | `app/routers/ai.py` | **工具循环**：调模型 → 执行工具 → 回填 → 直到出答案 | 见下 |
| 5.6 | `app/services/context.py` | `ToolContext` 的 MySQL 实现（接 `agent_tools.py`） | 工具能查到真数据 |

**5.5 核心循环**（整个产品的发动机）：
```python
while rounds < MAX_ROUNDS:                       # 上限防空转
    resp = await mimo.chat(messages, tools=TOOLS)
    if not resp.tool_calls:
        break
    for call in resp.tool_calls:
        result = executor.execute(call.name, call.args)
        await charge_for_tool(uid, call.name)     # 检索类免费
        messages.append(tool_msg(call.id, result.for_model()))
        await agent_log.record(call.name, result)
final = await critic.review(final_text)           # 合规终审
```

**5.4 演示关键**：`critic.py` 命中"稳赚"时要返回打回原因和位置，
并重写一次，两条日志都留。**这就是演示里"故意失败"的素材。**

**完成标志**：在 `/docs` 里发一句"给我一个能用的策略"，
返回里包含真实回测数字和基准对比，且被扣分。

---

## P6 · 前端（6h）

### 6.1 骨架（1h）

| 步骤 | 文件 | 做什么 |
|---|---|---|
| 6.1.1 | `frontend/package.json` | vue3 / vite / vue-router / pinia / axios / echarts |
| 6.1.2 | `frontend/vite.config.js` | `/api` 代理到 `127.0.0.1:8000` |
| 6.1.3 | `frontend/src/main.js` + `App.vue` | 挂载、引路由与 Pinia |
| 6.1.4 | `src/styles/base.css` | CSS 变量：色板、圆角、间距、字号 |
| 6.1.5 | `src/api/index.js` | axios 封装 + token 注入 + **402 拦截弹充值框** |
| 6.1.6 | `src/router/index.js` | 9 条路由 + 登录守卫 |
| 6.1.7 | `src/stores/user.js` `credits.js` | 用户与余额全局状态 |

```bash
cd frontend
npm config set registry https://registry.npmmirror.com
npm install && npm run dev        # http://localhost:5173
```

### 6.2 组件（1.5h）

| 文件 | 作用 |
|---|---|
| `components/NavBar.vue` | 顶栏：logo + 积分余额 + 登录态 |
| `components/CreditBadge.vue` | 余额胶囊，点击去充值 |
| `components/StrategyCard.vue` | 策略卡片（列表页复用） |
| `components/ParamForm.vue` | **按 `param_schema` 动态渲染参数**，省 20 个表单 |
| `components/EquityChart.vue` | ECharts 净值曲线 + 基准虚线 + 回撤面积图 |
| `components/MetricGrid.vue` | 指标九宫格（收益/回撤/夏普/胜率…） |
| `components/RechargeDialog.vue` | 充值弹窗（含收款码与后 6 位输入） |
| `components/InsufficientModal.vue` | 402 时弹出 |
| `components/ChatMessage.vue` | 对话气泡，支持渲染回测卡片 |
| `components/DataCutoff.vue` | **数据截止日期提示**，每个结果页都要有 |

### 6.3 页面（3.5h）

| 顺序 | 文件 | 页面 | 完成标志 |
|---|---|---|---|
| 1 | `views/StrategyList.vue` | 策略库 | 卡片能显示，筛选能用 |
| 2 | `views/StrategyDetail.vue` | 策略详情 | 参数表单能改，点回测能跳 |
| 3 | `views/BacktestResult.vue` | **回测结果** | 曲线 + 基准 + 指标 + 数据截止 |
| 4 | `views/Login.vue` | 登录注册 | 注册后右上角显示 100 积分 |
| 5 | `views/Credits.vue` | 积分中心 | 套餐 + 流水 + 充值弹窗 |
| 6 | `views/Chat.vue` | **AI 对话（主入口）** | 说人话能出回测卡片 |
| 7 | `views/Deployments.vue` | 观察列表 | 显示已保存的策略配置 |
| 8 | `views/Share.vue` | 分享只读页 `/s/:token` | 免登录能看报告 |
| 9 | `views/Landing.vue` | 落地页 | 教学 + 9.9 元 CTA |
| 10 | `views/Admin.vue` | 看板 | GMV / agent KPI / 事故 |

**做页面的顺序就是优先级顺序**，前三个做完产品就已经能看了。

---

## P7 · 运营闭环（3h）

| 步骤 | 文件 | 做什么 | 验证 |
|---|---|---|---|
| 7.1 | `scripts/gen_signals.py` | 每日生成观察列表信号 | 库里有今日信号 |
| 7.2 | `scripts/charge_deploy.py` | 部署日租扣费，不足自动暂停 | 余额不足时状态变 PAUSED |
| 7.3 | `scripts/check_ledger.py` | **账目一致性自查** | 余额 = 流水求和 |
| 7.4 | `scripts/weekly_report.py` | 一页纸经营周报（Markdown） | 生成 `hub/08-review.md` |
| 7.5 | `services/onboarding.py` | **欢迎邮件/欢迎卡**（第一单必需） | 充值成功后触发 |
| 7.6 | `routers/share.py` | 分享链接生成与只读访问 | 免登录可打开 |
| 7.7 | `scripts/backup.py` | mysqldump + 保留最近 7 份 | `backups/` 有文件 |
| 7.8 | `routers/admin.py` | 看板数据接口 | 前端 Admin 页有数 |

**7.5 欢迎邮件**：如果 SMTP 来不及配，降级为"充值成功后生成一张欢迎卡片 +
一段新手引导"，**但要有一个明确的交付动作**，不能支付完就完了。

---

## P8 · 卷宗补齐（1.5h）

| 文件 | 状态 | 做什么 |
|---|---|---|
| `hub/00-charter.md` | ✅ 已就绪 | 老板读一遍，确认拍板 |
| `hub/04-ops.md` | ✅ 已就绪 | 补充实际权限与密钥位置 |
| `hub/06-finance.md` | ✅ 已就绪 | 填真实经营数据 |
| `hub/01-market.md` | 待写 | AI 生成竞品表 + 定价带，你审 |
| `hub/02-brand.md` | 待写 | 命名、VI、语调、禁词 |
| `hub/03-product.md` | 待写 | 从 `docs/技术设计_详细版.md` 改写成产品说明 |
| `hub/05-pipeline.md` | 待写 | 内容台账 + 线索 + 订单，**用真实数据** |
| `hub/07-log.md` | 自动生成 | `agent_runs` 导出 + 截图索引 |
| `hub/08-review.md` | 自动生成 | 周报与复盘 |

> **红线**：没有数据就写"尚未产生"，禁止编造用户评价与营收数字。

---

## P9 · 部署与演练（2h）

| 步骤 | 文件/动作 | 关键点 |
|---|---|---|
| 9.1 | `deploy/nginx.conf` | `try_files` 支持 history 路由；**`proxy_buffering off`** 否则 SSE 卡死 |
| 9.2 | `deploy/quant.service` | systemd 托管 uvicorn |
| 9.3 | 时区设置 | `timedatectl set-timezone Asia/Shanghai` |
| 9.4 | `npm run build` → Nginx 托管 `dist/` | 不在服务器跑 Node |
| 9.5 | crontab | 拉数 18:00 / 信号 18:30 / 扣费 18:35 / 对账 02:00 |
| 9.6 | 全量备份 | `mysqldump` 到本地 + U 盘 |
| 9.7 | 断网演练 | 拔网线跑一遍 demo，确认不依赖实时接口 |
| 9.8 | 演示排练 3 遍 | 按官方 3 分钟脚本，含故意失败 |

### 官方 3 分钟演示脚本

| 时间 | 演什么 |
|---|---|
| 0:00-0:30 | 打开 `hub/`：「这是公司大脑，9 个卷宗，所有 Agent 共用同一份事实」 |
| 0:30-1:30 | 观众说"给我个策略"→ AI 找回测出报告 → 9.9 元充值 → **你手动确认到账** → 欢迎卡 |
| 1:30-2:15 | **故意失败**：报告出现"稳赚"→ 合规打回 → 重写 → 通过 → 展示两条日志 |
| 2:15-2:45 | 一键生成经营周报（GMV / 毛利 / agent 绩效 / 账目自查） |
| 2:45-3:00 | 「一个人，一支 AI 团队，开张并跑通第一单」 |

---

## 附：砍单顺序（做不完时从这里倒着砍）

| 顺序 | 砍什么 | 省下 | 代价 |
|---|---|---|---|
| 1 | `views/Admin.vue` + `routers/admin.py` | 1.5h | 答辩改跑 SQL，影响小 |
| 2 | `views/Deployments.vue` 及调度脚本 | 2h | 只保留回测主链路 |
| 3 | `views/Share.vue` + `routers/share.py` | 1h | 获客弱很多，慎砍 |
| 4 | `views/Landing.vue` 精简为单页 | 0.5h | 影响小 |
| 5 | 知识库从 20 篇减到 7 篇 | 1h | 护城河叙事变弱 |
| 6 | `critic.py` 改为纯禁词正则 | 1h | **不建议**，它是演示亮点 |
| 7 | 策略库从 9 个减到 4 个 | 0.5h | 影响小 |

### 死也不能砍

- 回测能当场出图
- 充值能收到真钱（哪怕人工核销）
- 积分流水能查
- 外网链接能打开
- Context Hub 卷宗（开场第一幕）

---

## 附：两人分工建议（如果有人搭手）

| 角色 | 负责 | 理由 |
|---|---|---|
| 你 | P0-P5、P8、P9 | 后端 + AI + 卷宗是评分主体，不能放手 |
| 搭档 | P6 前端、P7 运营脚本 | 相对独立，接口定好后可并行 |

**接口先定**：P4 做完就把 `/docs` 发给搭档，前后端就能并行，不用等。

---

## 🔧 2026-11-07 增量：AI 会自己选因子 + 部署上线

### 新增能力

| 模块 | 文件 | 说明 |
|---|---|---|
| 因子自动搜索 | `app/services/factor_search.py` | 遍历 16 个因子，按真实分布取阈值，贪心叠 3 个，含样本外验证 |
| 我的因子库 | `app/routers/saved_factors.py` + `schema_v3.sql` | 保存/改名/删除因子组合 |
| 部署包 | `app/services/deploy_pack.py` + `app/routers/deploy.py` | 固定模板生成 zip，含 Docker/每日任务/说明 |
| 前端 | `MyFactors.vue` / `Deploy.vue` / `FactorSearchCard.vue` / `DeployCard.vue` | 三个新页面组件 |
| AI 工具 | `auto_search_factors` / `list_factors` / `prepare_deploy` | 工具总数 12 |

### 关键设计取舍

**为什么搜索器要三道过拟合防线**：穷举 200 次必然能挑到历史最优，
第一版跑出 -28.55% → +31.94%（回撤 40%→3.16%），但持仓只剩 10.6%，
本质是"几乎不交易"的假象。现在限制持仓时间 ≥ 基线 60%，
并留 25% 数据做样本外验证，风险评级如实返回给 AI。

**为什么不代用户执行 SSH**：误操作不可逆，且看不到对方机器状态，出问题无法定位。
改成"生成包 + 分步引导"，三种入口（页面/回测页/AI 对话）。

**积分定价**：`auto_search_factors` 3 分、`design_strategy` 5 分、`check_robustness` 5 分。
原价 20 分导致用户在最核心功能上被劝退。

### 已验证

- pytest 23 passed
- AI 实测：自动调用 `search_strategies` → `auto_search_factors` → `run_backtest`，
  主动报告过拟合风险和反直觉发现（ADX 低反而更好，如实解释不圆场）
- 部署问答实测：输出三家云厂商价格表 + 三条边界
- 部署包 9157 bytes / 11 个文件，`daily.py` 与 `strategy.py` 语法检查通过
- 因子库增删改查全通

---

## 🌙 2026-11-07 深夜：AI 现场生成网页报告

### 这不是「固定页面 + 改参数」，是「AI 生成页面」

| | 策略实验室 `/lab` | 报告 `build_report` |
|---|---|---|
| 谁决定页面结构 | 我（开发者）写死 | **AI 每次根据对话决定** |
| 能改什么 | 参数数字 | 标题、模块、重点、结论、参数 |
| 输出 | 实时页面 | 可下载、可发人的 HTML + .py |
| 适合 | 调参探索 | 看完给结论、发给别人 |

AI 提供 `title / subtitle / sections[] / highlights[] / conclusion`，
系统用真实回测数据渲染成自包含 HTML（手写 SVG，不依赖任何 JS 库，断网可看）。

**安全边界**：AI 只填文字和模块选择，HTML 结构和样式由模板控制，
所有文本 `html.escape`，sections 走白名单 —— 不存在把 script 塞进报告的可能。

### 新增文件

| 文件 | 作用 |
|---|---|
| `app/services/report_builder.py` | SVG 图表 + HTML 模板（K线/净值/回撤/指标/交易/回滞） |
| `app/routers/reports.py` | 生成、公开访问、下载、脚本、列表、删除 |
| `schema_v4.sql` | `strategy_reports` 表 |
| `frontend/src/components/ReportCard.vue` | 对话里的报告卡片（含 iframe 预览） |
| `frontend/src/views/MyReports.vue` | `/reports` 我的报告 |

### 实测

- AI 对话「我想看茅台 1/99 均线加回滞 1% 的收益和回撤，做成网页给我看」
  → 自动调 `build_report` → 41 KB 报告，7 个模块，3 张 SVG
- 非均线策略（RSI）也能生成，30 KB
- 报告页 / 下载 HTML / 下载策略脚本 三个接口全通
- pytest 23 passed，51 个 Python 文件语法零错误

### 导航收敛

原来 8 个平铺链接太挤，改成：问AI / 策略库 / 实验室 / 因子库 / **我的 ▾**（策略·因子·报告·观察列表）/ 部署上线 / 经营看板
