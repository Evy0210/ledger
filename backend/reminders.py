"""日程提醒：跟 bot 说「提醒我明天吃饭的时候带小礼物」→ 到点在 Telegram 提醒。
时间用模型从口语里解析；没 key 或模型失败时走正则兜底（今天/明天/后天 + 几点）。
时间一律按伦敦本地时间存 YYYY-MM-DD HH:MM，scheduler 每分钟扫一次到期的。"""
import re
from datetime import datetime, timedelta

import database
import service
import vision

TRIGGER = re.compile(r"提醒我|提醒一下|记得提醒|remind me", re.I)
RESCHEDULE = re.compile(r"^(?:改到|改成|换到)\s*(.+)$")

PROMPT = """现在是 {now}（{weekday}，伦敦时间）。用户让你设一个提醒，原话：
「{text}」

严格输出一个 JSON 对象（不要 markdown，不要多余文字）：
{"due_at": "YYYY-MM-DD HH:MM", "what": "要提醒的事，简短，去掉「提醒我」和时间词", "time_guessed": true/false}

规则：
- 提醒要赶在事情之前，让人来得及准备：「吃饭的时候带礼物」要在出门前提醒，不是吃饭那一刻。
- 说了具体钟点（下午3点、18:30）就用它，time_guessed=false。
- 只有模糊时段时按下面取，time_guessed=true：早上 08:00；上午 09:00；中午 / 午饭 11:00；下午 14:00；
  傍晚 / 晚饭 17:00；晚上 19:00；睡前 22:00；「吃饭的时候」没说哪顿按 11:00。
- 只有日期没有时段：当天 09:00，time_guessed=true。
- 「X 分钟后 / X 小时后」按现在往后推，time_guessed=false。
- 「周五」指今天之后最近的那个周五；「下周五」指下一周的周五。
- 什么时间都没说：今天之后 1 小时，取整到整点或半点，time_guessed=true。"""

_DAY_WORD = {"今天": 0, "今晚": 0, "明天": 1, "明早": 1, "明晚": 1, "后天": 2, "大后天": 3}
_PERIOD = [("早上", 8), ("上午", 9), ("中午", 11), ("午饭", 11), ("下午", 14), ("傍晚", 17), ("晚饭", 17),
           ("晚上", 19), ("今晚", 19), ("明晚", 19), ("明早", 8), ("睡前", 22), ("吃饭", 11)]
_WEEKDAYS = "一二三四五六日"


def is_request(text: str) -> bool:
    return bool(TRIGGER.search(text))


def parse(text: str, now: datetime | None = None) -> dict:
    """→ {due_at, what, guessed}。解析出来的时间已经过了就顺延一天。"""
    now = now or service.now_local()
    out = None
    if vision.VISION_API_KEY:
        try:
            prompt = (PROMPT.replace("{now}", now.strftime("%Y-%m-%d %H:%M"))
                      .replace("{weekday}", "周" + _WEEKDAYS[now.weekday()]).replace("{text}", text.strip()[:300]))
            data = vision._extract_json(vision._chat(vision.TEXT_MODEL, prompt, timeout=30))
            due = datetime.strptime(str(data.get("due_at", "")).strip(), "%Y-%m-%d %H:%M")
            out = {"due": due, "what": str(data.get("what") or "").strip()[:200], "guessed": bool(data.get("time_guessed"))}
        except Exception:  # noqa: BLE001
            out = None
    if out is None:
        out = _fallback(text, now)
    naive_now = now.replace(tzinfo=None)
    if out["due"] <= naive_now:
        out["due"] += timedelta(days=1)
    if not out["what"]:
        out["what"] = _strip(text) or text.strip()[:200]
    return {"due_at": out["due"].strftime("%Y-%m-%d %H:%M"), "what": out["what"], "guessed": out["guessed"]}


def _fallback(text: str, now: datetime) -> dict:
    base = now.replace(tzinfo=None, second=0, microsecond=0)
    rel = re.search(r"(\d+|半)\s*(分钟|个?小时)(?:之?后|以后)", text)
    if rel:
        n = 0.5 if rel.group(1) == "半" else int(rel.group(1))
        delta = timedelta(minutes=n) if rel.group(2) == "分钟" else timedelta(hours=n)
        return {"due": base + delta, "what": _strip(text[:rel.start()] + text[rel.end():]), "guessed": False}
    offset = next((d for w, d in sorted(_DAY_WORD.items(), key=lambda kv: -len(kv[0])) if w in text), 0)
    m = re.search(r"(\d{1,2})\s*(?:[:：点])\s*(\d{1,2}|半)?", text)
    if m:
        hour, minute = int(m.group(1)), _minute(m.group(2))
        if hour < 12 and re.search(r"下午|傍晚|晚上|今晚|明晚", text):
            hour += 12
        due, guessed = (base + timedelta(days=offset)).replace(hour=hour % 24, minute=min(minute, 59)), False
    else:
        hour = next((h for w, h in _PERIOD if w in text), None)
        if hour is None and offset == 0:
            due = base + timedelta(hours=1)
            due = due.replace(minute=0 if due.minute < 30 else 30)
        else:
            due = (base + timedelta(days=offset)).replace(hour=hour or 9, minute=0)
        guessed = True
    return {"due": due, "what": _strip(text), "guessed": guessed}


def _minute(v: str | None) -> int:
    return 30 if v == "半" else int(v or 0)


def _strip(text: str) -> str:
    t = TRIGGER.sub("", text)
    t = re.sub(r"大后天|后天|明天|今天|今晚|明晚|明早|\d{1,2}\s*[:：点]\s*(\d{1,2}分?|半)?|早上|上午|中午|下午|傍晚|晚上|的时候", "", t)
    return t.strip(" ，,。.:：")[:200]


def parse_time_only(text: str, due_at: str, now: datetime | None = None) -> str | None:
    """「改到 18:00」「改到明天下午3点」→ 新的 due_at。只动说到的部分，日期没说就保留原来的日期。"""
    now = now or service.now_local()
    old = datetime.strptime(due_at, "%Y-%m-%d %H:%M")
    has_day = any(w in text for w in _DAY_WORD) or re.search(r"周[一二三四五六日天]|\d+\s*[号日]", text)
    if not has_day:
        m = re.search(r"(\d{1,2})\s*(?:[:：点])?\s*(\d{1,2}|半)?", text)
        if not m:
            return None
        hour, minute = int(m.group(1)), _minute(m.group(2))
        if hour < 12 and re.search(r"下午|傍晚|晚上", text):
            hour += 12
        if hour > 23 or minute > 59:
            return None
        return old.replace(hour=hour, minute=minute).strftime("%Y-%m-%d %H:%M")
    return parse(f"提醒我{text}", now)["due_at"]


def when_text(due_at: str, now: datetime | None = None) -> str:
    now = now or service.now_local()
    due = datetime.strptime(due_at, "%Y-%m-%d %H:%M")
    days = (due.date() - now.date()).days
    day = {0: "今天", 1: "明天", 2: "后天"}.get(days) or f"{due.month}月{due.day}日"
    return f"{day}（周{_WEEKDAYS[due.weekday()]}）{due:%H:%M}"


def create(text: str) -> tuple[dict, bool]:
    p = parse(text)
    rid = database.add_reminder(p["due_at"], p["what"])
    database.set_setting("reminder_last_created", str(rid))
    return database.get_reminder(rid), p["guessed"]


def last_created_pending() -> dict | None:
    rid = database.get_setting("reminder_last_created")
    r = database.get_reminder(int(rid)) if rid.isdigit() else None
    return r if r and r["status"] == "pending" else None
