"""
部署包生成 —— 一套模板，所有策略都往里套。

为什么不给每个策略单独写部署代码：那只会在服务器上留下一堆互不一致的脚本。
这里固定一份模板（Docker + systemd + 每日任务 + 说明），策略差异只体现为
strategy.py 顶部的参数和条件，换策略 = 换这几个数字，不用改部署逻辑。

包里的东西都能独立跑，不依赖我们的后端：
  strategy.py    回测验证脚本（可自己 pip install 跑）
  daily.py       每天拉一次数据、算今日信号、写 signals.json
  Dockerfile / docker-compose.yml
  deploy.sh      在服务器上一条命令装好并启动
  jragent.service  systemd 定时器（不想用 Docker 的走这条）
  README.md      买服务器、装环境、看日志、排错的全部步骤
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Any, Dict, List

try:
    from .exporter import build_script
except ImportError:
    from exporter import build_script


DAILY_PY = '''# -*- coding: utf-8 -*-
"""
每日信号任务 —— 模板文件，不需要改。

做的事：
  1. 拉最近 N 年日线
  2. 用 strategy.py 里定义的参数算信号
  3. 把「今天该买还是该卖」写进 signals.json，并打印出来

设计上有意做成「只出信号、不下单」：
任何自动下单都需要先确认券商的接口权限和风控规则，
本模板不碰钱，避免因为程序 bug 造成真实损失。
"""
import json
import os
import sys
from datetime import datetime

from strategy import load_bars, PARAMS, gen_signal, gen_filter_ok

SYMBOL = os.environ.get("SYMBOL", "{symbol}")
YEARS = int(os.environ.get("YEARS", "{years}"))
OUT = os.environ.get("OUT_FILE", "signals.json")


def main():
    print(f"[{{datetime.now():%Y-%m-%d %H:%M:%S}}] 拉取 {{SYMBOL}} 数据…")
    df = load_bars(SYMBOL, YEARS)
    if df is None or len(df) < 60:
        print("数据不足，跳过")
        sys.exit(1)

    df["sig"] = gen_signal(df, PARAMS)
    df["ok"] = gen_filter_ok(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    raw = int(last["sig"])
    raw_prev = int(prev["sig"])
    allowed = bool(last["ok"])

    if raw == 1 and raw_prev == 0 and allowed:
        action = "BUY"
        reason = "出现买入信号，且当前满足全部过滤条件"
    elif raw == 0 and raw_prev == 1:
        action = "SELL"
        reason = "出现卖出信号"
    elif raw == 1 and not allowed:
        action = "HOLD"
        reason = "有买入信号，但被过滤条件挡掉了（不追高/趋势不强）"
    else:
        action = "HOLD"
        reason = "没有新的信号"

    rec = {{
        "date": str(last.get("date", ""))[:10],
        "symbol": SYMBOL,
        "action": action,
        "close": round(float(last["close"]), 3),
        "reason": reason,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }}

    history = []
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    history.append(rec)
    history = history[-250:]          # 只留最近一年
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"  {{rec['date']}}  {{action}}  收盘 {{rec['close']}}")
    print(f"  理由：{{reason}}")


if __name__ == "__main__":
    main()
'''

REQUIREMENTS = """akshare>=1.12
pandas>=2.0
"""

DOCKERFILE = """# 固定版本，避免 Python 升级把依赖搞坏
FROM python:3.11-slim

WORKDIR /app
ENV TZ=Asia/Shanghai
ENV PYTHONUNBUFFERED=1

# 用国内镜像，海外服务器换回官方源
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i $PIP_INDEX

COPY . .

# 默认只跑一次，定时由宿主机 cron 或 docker 的 restart 策略控制
CMD ["python", "daily.py"]
"""

COMPOSE = """services:
  strategy:
    build: .
    container_name: jragent-strategy
    restart: unless-stopped
    environment:
      - SYMBOL={symbol}
      - YEARS={years}
      - TZ=Asia/Shanghai
    volumes:
      # 信号文件写到宿主机，方便直接看
      - ./data:/app/data
    command: python daily.py
"""

DEPLOY_SH = r"""#!/usr/bin/env bash
# 一键部署。在服务器上执行：bash deploy.sh
set -e

echo "=== 1/4 检查 Docker ==="
if ! command -v docker >/dev/null 2>&1; then
  echo "没装 Docker，开始安装（约 2 分钟）…"
  if command -v apt-get >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun
  elif command -v yum >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun
  else
    echo "系统太老，请手动装 Docker：https://docs.docker.com/engine/install/"
    exit 1
  fi
fi
docker --version

echo "=== 2/4 构建镜像 ==="
docker compose build || docker-compose build

echo "=== 3/4 先跑一次，确认能拿到数据 ==="
docker compose run --rm strategy

echo "=== 4/4 加每日定时（每个交易日 18:10）==="
CRON_LINE="10 18 * * 1-5 cd $(pwd) && docker compose up -d --build >> cron.log 2>&1"
( crontab -l 2>/dev/null | grep -v "jragent-strategy" ; echo "# jragent-strategy" ; echo "$CRON_LINE" ) | crontab -

echo
echo "部署完成。"
echo "  看信号：cat data/signals.json"
echo "  看日志：docker compose logs -f"
echo "  改参数：编辑 .env 后 docker compose up -d --build"
"""

SERVICE = """[Unit]
Description=JRAgent 策略每日信号
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={workdir}
ExecStart=/usr/bin/docker compose run --rm strategy
StandardOutput=append:{workdir}/cron.log
StandardError=append:{workdir}/cron.log
"""

TIMER = """[Unit]
Description=每个交易日 18:10 跑一次策略

[Timer]
OnCalendar=Mon..Fri 18:10
Persistent=true

[Install]
WantedBy=timers.target
"""

ENV_EXAMPLE = """# 标的代码（6 位）
SYMBOL={symbol}
# 回看年数
YEARS={years}
# 想换参数直接改下面两个（均线策略才有）
# FAST=5
# SLOW=20
"""


def _readme(payload: Dict[str, Any]) -> str:
    symbol = payload.get("symbol_code", "600519")
    years = payload.get("years", 3)
    name = payload.get("strategy_name", payload.get("strategy_type", "策略"))
    cond = payload.get("condition_text") or "无额外过滤条件"
    now = datetime.now().strftime("%Y-%m-%d")

    return f"""# {name} · 部署包

生成时间：{now}
标的：{symbol}　回看：{years} 年
因子条件：{cond}

这个包里是一个**能独立运行**的每日信号任务。不依赖全域策略研究所的服务器，
断网也能跑（只要有行情接口）。

---

## 一、先买一台服务器

不需要很贵的。这个任务一次运行只占用几百 MB 内存、跑十几秒。

| 场景 | 配置 | 参考价格 |
|---|---|---|
| 只跑这一个策略 | 2 核 2G，40G 系统盘 | 阿里云/腾讯云轻量应用服务器，约 ¥60-90/年（新用户） |
| 想跑多个策略 + 存数据 | 2 核 4G，60G | 约 ¥200-400/年 |
| 已经有服务器 | 直接用，能装 Docker 就行 | — |

购买时选：
- **地域**：国内（上海/杭州/北京），访问行情接口快
- **镜像**：Ubuntu 22.04 或 24.04（最省事）
- **带宽**：1-3 Mbps 够用，这个程序不对外提供服务
- **安全组**：只需要开 SSH(22)，**不要开 80/443**，程序不需要对外暴露

腾讯云「轻量应用服务器」和阿里云「轻量应用服务器」都有新手引导，
跟着点就行。买完会给你一个公网 IP 和 root 密码。

---

## 二、把包传上去

在你自己电脑上（不是服务器上）执行：

```bash
# 用 scp，把整个目录传上去
scp -r jragent-strategy root@你的服务器IP:/opt/
```

Windows 没有 scp 的话，用 Xshell / FinalShell / WinSCP 拖进去也行。

---

## 三、一条命令部署

```bash
ssh root@你的服务器IP
cd /opt/jragent-strategy
bash deploy.sh
```

`deploy.sh` 会自动：装 Docker（如果没有）→ 构建镜像 → 先跑一次验证 →
加每日 18:10 的定时任务。

看到 `部署完成` 就好了。

---

## 四、看结果

```bash
# 今天的信号
cat data/signals.json

# 运行日志
docker compose logs -f

# 手动跑一次
docker compose run --rm strategy
```

`signals.json` 长这样：

```json
[
  {{
    "date": "2026-11-06",
    "symbol": "{symbol}",
    "action": "HOLD",
    "close": 1454.11,
    "reason": "没有新的信号"
  }}
]
```

`action` 只有三种：`BUY`（今天该买）、`SELL`（今天该卖）、`HOLD`（不动）。

---

## 五、改参数

编辑 `.env`：

```bash
vi .env
```

改完重建：

```bash
docker compose up -d --build
```

---

## 六、常见问题

**拉不到数据 / 报网络错误**
A 股数据走的是公开接口，服务器在境外可能被限。换国内地域，或者多试几次。

**容器起来了但 signals.json 没更新**
先手动跑一次看报什么错：`docker compose run --rm strategy`

**想看历史信号**
`data/signals.json` 保留最近 250 个交易日。

**要不要开防火墙端口**
不用。这个程序只往外拉数据，不提供服务端口。

---

## 七、这个包不做什么

- **不下单**。自动交易需要券商接口权限和风控，本模板刻意不碰钱。
- **不保证赚钱**。它只是把你在平台上验证过的规则，原样搬到服务器上每天执行。
- **不监控**。没有告警，出错了要自己看日志。需要告警可以自己加个 webhook。

---

## 八、文件说明

| 文件 | 用途 |
|---|---|
| `strategy.py` | 回测验证脚本，`python strategy.py --symbol {symbol}` 可跑 |
| `daily.py` | 每日任务，算出今天的信号 |
| `deploy.sh` | 一键部署 |
| `Dockerfile` / `docker-compose.yml` | 容器化，保证环境一致 |
| `jragent.service` / `jragent.timer` | 不想用 cron 的话走 systemd |
| `.env` | 参数配置 |
"""


def build_package(payload: Dict[str, Any]) -> bytes:
    """返回 zip 的二进制内容，直接给前端下载"""
    symbol = payload.get("symbol_code", "600519")
    years = int(payload.get("years", 3))

    script = build_script(payload)

    files: Dict[str, str] = {
        "strategy.py": script,
        "daily.py": DAILY_PY.format(symbol=symbol, years=years),
        "requirements.txt": REQUIREMENTS,
        "Dockerfile": DOCKERFILE,
        "docker-compose.yml": COMPOSE.format(symbol=symbol, years=years),
        "deploy.sh": DEPLOY_SH,
        "jragent.service": SERVICE.format(workdir="/opt/jragent-strategy"),
        "jragent.timer": TIMER,
        ".env": ENV_EXAMPLE.format(symbol=symbol, years=years),
        ".env.example": ENV_EXAMPLE.format(symbol=symbol, years=years),
        "README.md": _readme(payload),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            # 统一用 UTF-8 + \n，避免在 Linux 上出现 CRLF 导致 bash 报错
            z.writestr(f"jragent-strategy/{path}",
                       content.replace("\r\n", "\n").encode("utf-8"))
    return buf.getvalue()


PLATFORMS: List[Dict[str, Any]] = [
    {
        "name": "腾讯云 轻量应用服务器",
        "url": "https://cloud.tencent.com/product/lighthouse",
        "spec": "2 核 2G / 40G / 1Mbps",
        "price": "新用户约 ¥70-100/年",
        "note": "新手引导最清楚，选 Ubuntu 22.04 镜像，买完直接有 root 密码",
    },
    {
        "name": "阿里云 轻量应用服务器",
        "url": "https://www.aliyun.com/product/swas",
        "spec": "2 核 2G / 40G / 3Mbps",
        "price": "新用户约 ¥80-120/年",
        "note": "国内访问行情接口最稳，镜像选 Ubuntu 24.04",
    },
    {
        "name": "华为云 弹性云服务器",
        "url": "https://www.huaweicloud.com/product/ecs.html",
        "spec": "1 核 2G / 40G",
        "price": "按需付费，约 ¥15/月",
        "note": "适合已经有华为云账号的，选鲲鹏或 x86 都行",
    },
]

STEPS: List[Dict[str, Any]] = [
    {"step": 1, "title": "买服务器", "detail": "选 2 核 2G、Ubuntu 22.04、国内地域。安全组只开 22 端口，不要开 80/443。"},
    {"step": 2, "title": "下载部署包", "detail": "在回测结果页或「部署上线」页点下载，得到一个 zip。里面是策略代码 + Docker 配置 + 一键脚本。"},
    {"step": 3, "title": "上传并执行", "detail": "scp -r jragent-strategy root@服务器IP:/opt/ ，然后 ssh 上去执行 cd /opt/jragent-strategy && bash deploy.sh。脚本会自动装 Docker 并加每日定时。"},
    {"step": 4, "title": "看结果", "detail": "cat data/signals.json 看今天的信号；docker compose logs -f 看运行日志。"},
]
