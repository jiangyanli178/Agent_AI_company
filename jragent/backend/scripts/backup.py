# -*- coding: utf-8 -*-
"""
数据库备份：mysqldump + 保留最近 7 份。

演示前必须手动跑一次。现场数据库挂了就全完。

用法：
    python scripts/backup.py
"""
import os
import subprocess
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

KEEP = 7


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    backup_dir = os.path.join(os.path.dirname(root), "backups")
    os.makedirs(backup_dir, exist_ok=True)

    db = os.getenv("DB_NAME", "quant_shop")
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    host = os.getenv("DB_HOST", "127.0.0.1")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(backup_dir, f"{db}_{ts}.sql")

    cmd = [
        "mysqldump",
        f"--host={host}",
        f"--user={user}",
        f"--password={password}",
        "--single-transaction",      # InnoDB 一致性备份，不锁表
        "--routines",
        "--databases", db,
    ]
    # Windows 上 mysqldump 可能不在 PATH，尝试常见安装路径
    if os.name == "nt":
        for base in (r"C:\Program Files\MySQL", r"C:\Program Files (x86)\MySQL"):
            if os.path.isdir(base):
                for ver in os.listdir(base):
                    cand = os.path.join(base, ver, "bin", "mysqldump.exe")
                    if os.path.exists(cand):
                        cmd[0] = cand
                        break

    print(f"开始备份 {db} → {out}")
    t = time.time()
    with open(out, "w", encoding="utf-8") as f:
        # 注意：密码会出现在命令行里，本机备份可接受；生产请改用 .my.cnf
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        print("备份失败：", proc.stderr[:500])
        print("如果是 mysqldump 找不到，请把 MySQL 的 bin 目录加入 PATH 后重试")
        raise SystemExit(1)

    size = os.path.getsize(out) / 1024
    print(f"备份完成，耗时 {time.time()-t:.1f}s，大小 {size:.1f} KB")

    # 只保留最近 KEEP 份
    files = sorted(f for f in os.listdir(backup_dir) if f.endswith(".sql"))
    for old in files[:-KEEP]:
        os.remove(os.path.join(backup_dir, old))
        print(f"已清理旧备份：{old}")

    print(f"当前共保留 {min(len(files), KEEP)} 份备份，目录：{backup_dir}")


if __name__ == "__main__":
    main()
