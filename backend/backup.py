"""每日备份：由 K3s CronJob ledger-backup 调用（见 deploy/k8s.yaml）。

用 SQLite 在线备份 API 拷一份一致的 ledger.db，小票图打成 tar.gz，放到 BACKUP_DIR/日期/ 下；
超过 BACKUP_KEEP_DAYS 天的旧备份删掉。成功后写 BACKUP_DIR/last_ok，监控据此判断备份是否按时完成。"""
import os
import shutil
import sqlite3
import tarfile
import time
from datetime import date
from pathlib import Path

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/data"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backup"))
KEEP_DAYS = int(os.environ.get("BACKUP_KEEP_DAYS", "30"))


def main():
    out = BACKUP_DIR / date.today().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(DATA_ROOT / "ledger.db")
    dst = sqlite3.connect(out / "ledger.db")
    src.backup(dst)
    n = dst.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
    dst.close()
    src.close()
    receipts = DATA_ROOT / "receipts"
    if receipts.is_dir():
        with tarfile.open(out / "receipts.tar.gz", "w:gz") as tar:
            tar.add(receipts, arcname="receipts")
    cutoff = time.time() - KEEP_DAYS * 86400
    for old in BACKUP_DIR.iterdir():
        if old.is_dir() and old != out and old.stat().st_mtime < cutoff:
            shutil.rmtree(old)
    (BACKUP_DIR / "last_ok").write_text(f"{int(time.time())} {n}\n")
    print(f"backup ok: {out} ({n} expenses)")


if __name__ == "__main__":
    main()
