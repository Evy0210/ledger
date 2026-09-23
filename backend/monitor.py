"""运行监控：由 K3s CronJob 每 10 分钟跑一次（见 deploy/k8s.yaml 的 ledger-monitor）。

检查后端、演示后端、前端、公网 HTTPS（含证书剩余天数）、磁盘和每日备份；状态变化时发 Telegram：
出故障发一条，持续故障每 MONITOR_REPEAT_HOURS 小时再提醒一次，恢复时再发一条。
上次的状态存在 MONITOR_STATE（hostPath 挂进来的一个 JSON 文件），所以不会每 10 分钟刷屏。

    python monitor.py            # 跑一次检查
    python monitor.py --test     # 不管状态，发一条测试消息
"""
import json
import os
import shutil
import socket
import ssl
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("MONITOR_CHAT_ID", "") or os.environ.get("TELEGRAM_CHAT_ID", "")
STATE_PATH = os.environ.get("MONITOR_STATE", "/state/ledger-monitor.json")
REPEAT_HOURS = float(os.environ.get("MONITOR_REPEAT_HOURS", "6"))
CERT_WARN_DAYS = int(os.environ.get("MONITOR_CERT_WARN_DAYS", "14"))
DISK_WARN_PCT = int(os.environ.get("MONITOR_DISK_WARN_PCT", "90"))
# 可选：备份目录（backup.py 成功后写 last_ok）。超过 BACKUP_MAX_HOURS 没有新备份就告警。
BACKUP_MARK = os.environ.get("MONITOR_BACKUP_MARK", "")
BACKUP_MAX_HOURS = float(os.environ.get("MONITOR_BACKUP_MAX_HOURS", "30"))
# 「名字=URL」逗号分隔；URL 返回 2xx 即正常
TARGETS = [t.split("=", 1) for t in os.environ.get("MONITOR_TARGETS", "").split(",") if "=" in t]
PUBLIC_URL = os.environ.get("MONITOR_PUBLIC_URL", "").rstrip("/")


def check_http(url: str) -> str:
    """'' = 正常，否则是问题描述。"""
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
    except httpx.HTTPError as e:
        return f"连不上（{type(e).__name__}）"
    return "" if r.status_code < 300 else f"HTTP {r.status_code}"


def check_cert(url: str) -> str:
    host = urlparse(url).hostname
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as sock, ctx.wrap_socket(sock, server_hostname=host) as s:
            not_after = s.getpeercert()["notAfter"]
    except (OSError, ssl.SSLError) as e:
        return f"证书检查失败（{type(e).__name__}）"
    expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    days = (expires - datetime.now(timezone.utc)).days
    return f"证书 {days} 天后过期" if days < CERT_WARN_DAYS else ""


def check_disk(path: str = "/") -> str:
    u = shutil.disk_usage(path)
    pct = u.used * 100 // u.total
    return f"磁盘已用 {pct}%" if pct >= DISK_WARN_PCT else ""


def check_backup(mark: str) -> str:
    try:
        ts = int(open(mark).read().split()[0])
    except (OSError, ValueError, IndexError):
        return "找不到备份记录"
    hours = (time.time() - ts) / 3600
    return f"已经 {hours:.0f} 小时没有新备份" if hours > BACKUP_MAX_HOURS else ""


def run_checks() -> dict[str, str]:
    results = {name: check_http(url) for name, url in TARGETS}
    if PUBLIC_URL:
        results["公网"] = check_http(PUBLIC_URL + "/login")
        results["证书"] = check_cert(PUBLIC_URL)
    results["磁盘"] = check_disk()
    if BACKUP_MARK:
        results["备份"] = check_backup(BACKUP_MARK)
    return results


def send(text: str) -> bool:
    if not (BOT_TOKEN and CHAT_ID):
        print("no TELEGRAM_BOT_TOKEN / MONITOR_CHAT_ID, message not sent:\n" + text)
        return False
    r = httpx.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                   json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=15)
    return r.status_code == 200


def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, ensure_ascii=False)


def main():
    if "--test" in sys.argv:
        ok = send("🩺 记账本监控：测试消息，告警会发到这里。")
        print("sent" if ok else "not sent")
        return
    now = time.time()
    results = run_checks()
    state = load_state()               # {名字: {"since": ts, "alerted": ts, "detail": str}}
    down, recovered = [], []
    for name, problem in results.items():
        prev = state.get(name)
        if problem:
            if not prev:
                state[name] = {"since": now, "alerted": now, "detail": problem}
                down.append(f"❌ {name}：{problem}")
            elif now - prev["alerted"] >= REPEAT_HOURS * 3600:
                prev.update(alerted=now, detail=problem)
                hours = (now - prev["since"]) / 3600
                down.append(f"❌ {name}：{problem}（已持续 {hours:.0f} 小时）")
            else:
                prev["detail"] = problem
        elif prev:
            minutes = (now - prev["since"]) / 60
            recovered.append(f"✅ {name} 恢复了（中断约 {minutes:.0f} 分钟）")
            state.pop(name)
    save_state(state)
    print(json.dumps(results, ensure_ascii=False))
    if down or recovered:
        send("🩺 记账本监控\n" + "\n".join(down + recovered))


if __name__ == "__main__":
    main()
