"""提醒调度：每分钟看一眼伦敦时间。
- 每天 reminder_hour 点：今天还没记账就催一下（mode 可选每天 / 工作日 / 每周日）
- 每月 1 号 09:00：推上月汇总
- 用户让 bot 设的日程提醒：到点就发
去重靠 settings 里记「上次发送的日期」。"""
import asyncio
import logging
from datetime import timedelta

import database
import pantry
import reminders
import service
import telegram

log = logging.getLogger("ledger.scheduler")


async def run():
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception as e:  # noqa: BLE001
            log.warning("tick failed: %s", e)
        await asyncio.sleep(60)


def tick():
    if not telegram.enabled() or not telegram.bound_chat_id():
        return
    now = service.now_local()
    today = now.date().isoformat()
    s = database.all_settings()

    for r in database.due_reminders(now.strftime("%Y-%m-%d %H:%M")):
        # 发成功才标已发；Telegram 暂时连不上就下一分钟再试
        late = r["due_at"] < (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
        if telegram.notify(f"⏰ 提醒：{service._esc(r['text'])}"
                           + (f"\n（原定 {reminders.when_text(r['due_at'], now)}，服务器刚恢复，晚了）" if late else "")):
            database.update_reminder(r["id"], status="sent")

    if s.get("reminder_enabled") == "1" and now.hour == int(s.get("reminder_hour") or 21):
        if database.get_setting("reminder_last_sent") != today and _due_today(s.get("reminder_mode"), now.weekday()):
            database.set_setting("reminder_last_sent", today)
            if database.count_on_day(today) == 0:
                st = service.status()
                gap = st["days_since_last"]
                tail = f"已经 {gap} 天没记了 🙈" if gap > 1 else ""
                telegram.notify(f"🔔 今天还没记账～ {tail}\n发张小票，或者一句「12.5 coffee」给我就行。")

    # 17:00 看一眼冰箱：2 天内到期的食材提醒做菜
    if s.get("pantry_reminder", "1") == "1" and now.hour == int(s.get("pantry_hour") or 17):
        if database.get_setting("pantry_last_sent") != today:
            database.set_setting("pantry_last_sent", today)
            text = pantry.expiring_text(now.date())
            if text:
                telegram.notify(text)

    if s.get("monthly_report") == "1" and now.day == 1 and now.hour >= 9:
        month = now.strftime("%Y-%m")
        if database.get_setting("report_last_sent") != month:
            database.set_setting("report_last_sent", month)
            prev = service._prev_month(month)
            telegram.notify("上个月的账算好了 👇\n\n" + service.month_report_text(prev))


def _due_today(mode: str | None, weekday: int) -> bool:
    if mode == "weekdays":
        return weekday < 5
    if mode == "weekly":
        return weekday == 6
    return True
