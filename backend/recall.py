"""银行有扣款、但没小票的记录：问一句「这是什么」，想起来了一句话补上。

- 「没说明的」= 银行短信 / 流水补记的（source=sms）且还没有明细。补完会加一行明细，就不再算没说明。
- 每天 recall_hour 点（默认 20 点）把新出现的这类记录发一次，同一笔只主动问一次；/recall 随时看全部。
- 补充：「11.21 那笔是摆渡车的钱」「昨天 TfL 那笔是地铁」「第 2 笔是给室友带的咖啡」。
  「11.21」既可能是 11 月 21 日也可能是 £11.21，两种都去找，哪种对上算哪种。
"""
import json
import re
from datetime import date, timedelta

import database
import service

ASKED_KEY = "recall_asked"           # 已经主动问过的账单 id
LIST_KEY = "recall_list"             # 最近一次列出来的编号 → 账单 id，给「第 2 笔是…」用

TRIGGER = re.compile(r"^(?P<sel>.{0,40}?)(?:那一?笔|这一?笔|那个)(?:钱|消费|扣款|支出)?\s*(?:是|=|：|:)\s*(?P<desc>.+)$", re.S)
NTH = re.compile(r"^\s*第\s*(?P<n>\d{1,2})\s*(?:笔|个|条)\s*(?:是|=|：|:)\s*(?P<desc>.+)$", re.S)
_REL = {"今天": 0, "昨天": 1, "前天": 2, "大前天": 3}
_MD = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:[./]|月)\s*(\d{1,2})\s*[日号]?(?![\d])")
_DAY_ONLY = re.compile(r"(?<![\d.月])(\d{1,2})\s*[号日]")
_MONEY = re.compile(r"(?:£|￡|gbp\s*)(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*(?:镑|磅|gbp)", re.I)
_DESC_TAIL = re.compile(r"(?:的)?(?:钱|费用|消费|开销|支出)$")


def is_note(text: str) -> bool:
    return bool(NTH.match(text) or TRIGGER.match(text))


def unexplained() -> list[dict]:
    """银行补记、还不知道是什么的账单，新的在前。"""
    rows = database.list_expenses(start=(service.today() - timedelta(days=400)).isoformat(),
                                  end=service.today().isoformat())
    return [e for e in rows if e["source"] == "sms" and not e["items"]]


def remember_list(rows: list[dict]):
    database.set_setting(LIST_KEY, json.dumps([e["id"] for e in rows]))


def mark_asked(ids: list[int]):
    old = json.loads(database.get_setting(ASKED_KEY) or "[]")
    database.set_setting(ASKED_KEY, json.dumps((old + [i for i in ids if i not in old])[-500:]))


def to_ask() -> list[dict]:
    """还没主动问过的那些。"""
    asked = set(json.loads(database.get_setting(ASKED_KEY) or "[]"))
    return [e for e in unexplained() if e["id"] not in asked]


def line(k: int, e: dict) -> str:
    bank = re.sub(r"^信用卡短信：?", "", e["note"]).split(" · ")[0].strip() or e["merchant"] or "—"
    return f"{k}. {e['date'][5:].replace('-', '.')}  £{e['amount_gbp']:.2f}  {service._esc(bank)}"


def list_text(rows: list[dict], intro: str) -> str:
    lines = [intro, ""] + [line(k, e) for k, e in enumerate(rows, 1)]
    lines.append("\n想起来是什么就回我「第 1 笔是摆渡车」，或者「11.21 那笔是摆渡车的钱」（说日期或金额都行）。")
    return "\n".join(lines)


# ---- 找是哪一笔 ------------------------------------------------------------

def _past_date(month: int, day: int, today: date) -> date | None:
    """「11.21」→ 最近一个已经过去的 11 月 21 日。"""
    for y in (today.year, today.year - 1):
        try:
            d = date(y, month, day)
        except ValueError:
            return None
        if d <= today:
            return d
    return None


def find(selector: str) -> list[dict]:
    """从「11.21」「昨天 TfL」「£3.50」这类说法里找账单。没说明的优先。

    明说的日期（昨天、11月21日、21号、11/21）和金额（£3.5、3.5 镑）都必须对上；
    「11.21」这种带点的两种读法都试，日期或金额对上一个就行。"""
    today = service.today()
    rest = selector.strip()
    days: set[str] = set()
    amounts: set[float] = set()
    either: list[tuple[str, float]] = []
    for w in sorted(_REL, key=len, reverse=True):
        if w in rest:
            days.add((today - timedelta(days=_REL[w])).isoformat())
            rest = rest.replace(w, " ")
    for m in _MONEY.finditer(rest):
        amounts.add(float(m.group(1) or m.group(2)))
    rest = _MONEY.sub(" ", rest)
    for m in _MD.finditer(rest):
        d = _past_date(int(m.group(1)), int(m.group(2)), today)
        if "." in m.group(0):
            either.append((d.isoformat() if d else "", float(f"{m.group(1)}.{m.group(2)}")))
        elif d:
            days.add(d.isoformat())
    rest = _MD.sub(" ", rest)
    for m in _DAY_ONLY.finditer(rest):
        d = _past_date(today.month, int(m.group(1)), today) or _past_date((today.month - 2) % 12 + 1, int(m.group(1)), today)
        if d:
            days.add(d.isoformat())
    words = service._tokens(_DAY_ONLY.sub(" ", rest))
    if not (days or amounts or either or words):
        return []

    def same(e: dict, a: float) -> bool:
        return abs(e["amount_gbp"] - a) < 0.005 or abs(e["amount"] - a) < 0.005

    def hit(e: dict) -> bool:
        if days and e["date"] not in days:
            return False
        if amounts and not any(same(e, a) for a in amounts):
            return False
        if either and not any(e["date"] == d or same(e, a) for d, a in either):
            return False
        return not words or bool(words & service._tokens(e["merchant"] + " " + e["note"]))

    pool = database.list_expenses(start=(today - timedelta(days=400)).isoformat(), end=today.isoformat())
    found = [e for e in pool if hit(e)]
    vague = [e for e in found if e["source"] == "sms" and not e["items"]]
    return vague or found


def _fillable(e: dict) -> bool:
    """银行补记的、还没明细，或者明细就是上次用这里补的那一行。"""
    if e["source"] != "sms":
        return False
    return not e["items"] or (len(e["items"]) == 1 and "补充：" in e["note"]
                              and abs(e["items"][0].get("amount", 0) - e["amount"]) < 0.005)


def pick(text: str) -> tuple[list[dict], str]:
    """一句补充 → (候选账单, 说明)。"""
    m = NTH.match(text)
    if m:
        ids = json.loads(database.get_setting(LIST_KEY) or "[]")
        n = int(m.group("n"))
        e = database.get_expense(ids[n - 1]) if 1 <= n <= len(ids) else None
        return ([e] if e else []), m.group("desc").strip()
    m = TRIGGER.match(text)
    return find(m.group("sel")), m.group("desc").strip()


# ---- 补上 ----------------------------------------------------------------

def apply(e: dict, desc: str, classify) -> dict:
    """没说明的（或者之前就是这样补的，再说一遍 = 改口）：按这句话定商家 / 分类，
    加一行明细（整笔金额），银行原文留在备注里。其他有明细的：只把这句话追加进备注。"""
    desc = desc.strip(" 。.!！")[:120]
    name = _DESC_TAIL.sub("", desc).strip() or desc
    if _fillable(e):
        guess = classify(name)
        bank = e["note"].split(" · 补充：")[0]
        payload = {**e, "merchant": guess["merchant"] or name, "category": guess["category"],
                   "items": [{"name": name, "qty": 1, "amount": e["amount"], "category": guess["category"],
                              "sub": guess.get("sub", "")}],
                   "note": (bank + " · " if bank else "") + f"补充：{desc}"}
    else:
        payload = {**e, "note": ((e["note"] + " · ") if e["note"] else "") + f"补充：{desc}"}
    payload["note"] = payload["note"][:300]
    database.update_expense(e["id"], service.build_expense(payload, source=e["source"], image=e["image"]))
    return database.get_expense(e["id"])
