"""SQLite storage. Connections are short-lived (open → commit → close) so a
stuck request can never hold the write lock — same lesson as recipes."""
import json
import sqlite3
import time
from collections import defaultdict

from categories import CATEGORIES, SUB_LABEL, coerce_category, coerce_sub
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,                     -- YYYY-MM-DD（伦敦本地日期）
    merchant    TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'other',     -- 整笔的主分类
    currency    TEXT NOT NULL DEFAULT 'GBP',       -- 小票上的原币
    amount      REAL NOT NULL,                     -- 原币金额
    amount_gbp  REAL NOT NULL,                     -- 折成英镑（本位币）
    amount_cny  REAL NOT NULL,                     -- 折成人民币（按当天汇率锁定）
    fx_gbp_cny  REAL NOT NULL,                     -- 当天 GBP→CNY
    items       TEXT NOT NULL DEFAULT '[]',        -- [{name, qty, amount, category}] 原币
    note        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'manual',    -- manual | receipt | telegram
    image       TEXT NOT NULL DEFAULT '',          -- receipts/ 下的文件名
    order_ref   TEXT NOT NULL DEFAULT '',          -- 订单号，用来把邮件和截图合并成一笔
    split_kind  TEXT NOT NULL DEFAULT '',          -- '' 自己的 | split 和室友分 | paid_for 代付（不算我的支出）
    split_n     INTEGER NOT NULL DEFAULT 1,        -- 分几个人（含我）
    split_with  TEXT NOT NULL DEFAULT '',          -- 和谁分，随便写
    settled     INTEGER NOT NULL DEFAULT 0,        -- 别人那份已经还我了
    share_gbp   REAL,                              -- 我自己那份（按商品级 who 算好存起来）
    share_cny   REAL,
    owed_gbp    REAL,                              -- 别人还没还我的
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses (date);
CREATE TABLE IF NOT EXISTS fx_rates (
    day   TEXT NOT NULL,
    pair  TEXT NOT NULL,                            -- 'GBPCNY'
    rate  REAL NOT NULL,
    PRIMARY KEY (day, pair)
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbound_emails (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT NOT NULL DEFAULT '',
    subject     TEXT NOT NULL DEFAULT '',
    sender      TEXT NOT NULL DEFAULT '',
    order_ref   TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL,                     -- ok | skipped | error
    detail      TEXT NOT NULL DEFAULT '',
    expense_id  INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbound_message ON inbound_emails (message_id);
CREATE TABLE IF NOT EXISTS pantry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id  INTEGER NOT NULL,
    item_idx    INTEGER NOT NULL,                  -- expenses.items 里的下标
    name        TEXT NOT NULL,                     -- 显示名（中文优先）
    name_raw    TEXT NOT NULL DEFAULT '',
    sub         TEXT NOT NULL DEFAULT '',
    qty         REAL NOT NULL DEFAULT 1,
    bought      TEXT NOT NULL,                     -- YYYY-MM-DD
    expires     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'in',        -- in | used | tossed
    updated_at  INTEGER NOT NULL,
    UNIQUE (expense_id, item_idx)
);
"""

# 默认设置；网页设置页可改，Telegram 绑定也存这里。
DEFAULT_SETTINGS = {
    "reminder_enabled": "1",
    "reminder_hour": "21",         # 伦敦时间几点提醒
    "reminder_mode": "daily",      # daily | weekdays | weekly（周日）
    "monthly_report": "1",         # 每月 1 号早上推上月汇总
    "budget_gbp": "",              # 月预算，空=不设
    "pantry_reminder": "1",        # 食材快过期提醒
    "pantry_hour": "17",
    "telegram_chat_id": "",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(expenses)")}
        if "order_ref" not in cols:
            conn.execute("ALTER TABLE expenses ADD COLUMN order_ref TEXT NOT NULL DEFAULT ''")
        for col, ddl in (("split_kind", "TEXT NOT NULL DEFAULT ''"), ("split_n", "INTEGER NOT NULL DEFAULT 1"),
                         ("split_with", "TEXT NOT NULL DEFAULT ''"), ("settled", "INTEGER NOT NULL DEFAULT 0"),
                         ("share_gbp", "REAL"), ("share_cny", "REAL"), ("owed_gbp", "REAL")):
            if col not in cols:
                conn.execute(f"ALTER TABLE expenses ADD COLUMN {col} {ddl}")
        # 老数据补算份额
        for row in conn.execute("SELECT * FROM expenses WHERE share_gbp IS NULL").fetchall():
            d = dict(row); d["items"] = json.loads(d["items"] or "[]")
            sh = compute_share(d)
            conn.execute("UPDATE expenses SET share_gbp=?, share_cny=?, owed_gbp=? WHERE id=?",
                         (sh["share_gbp"], sh["share_cny"], sh["owed_gbp"], d["id"]))
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_ref ON expenses (order_ref)")
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))


# ---- settings ----------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))


def all_settings() -> dict:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in rows})
    return out


# ---- fx cache ----------------------------------------------------------

def get_fx(day: str, pair: str) -> float | None:
    with _connect() as conn:
        row = conn.execute("SELECT rate FROM fx_rates WHERE day = ? AND pair = ?", (day, pair)).fetchone()
    return row["rate"] if row else None


def latest_fx(pair: str) -> float | None:
    with _connect() as conn:
        row = conn.execute("SELECT rate FROM fx_rates WHERE pair = ? ORDER BY day DESC LIMIT 1", (pair,)).fetchone()
    return row["rate"] if row else None


def set_fx(day: str, pair: str, rate: float):
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO fx_rates (day, pair, rate) VALUES (?, ?, ?)", (day, pair, rate))


# ---- expenses ----------------------------------------------------------

# 我自己那份。分账时按商品级 who 算：me = 全归我，them = 全归别人，shared（默认）= 1/n。
SHARE_SQL = "COALESCE(share_gbp, amount_gbp)"
SHARE_CNY_SQL = "COALESCE(share_cny, amount_cny)"


def compute_share(e: dict) -> dict:
    amount, cny = float(e["amount_gbp"]), float(e["amount_cny"])
    kind, n = e.get("split_kind") or "", max(int(e.get("split_n") or 1), 1)
    settled = bool(e.get("settled"))
    if kind == "paid_for":
        ratio = 0.0
    elif kind == "split":
        items = [i for i in (e.get("items") or []) if (i.get("amount") or 0) > 0]
        item_sum = sum(i["amount"] for i in items)
        if items and item_sum > 0 and any(i.get("who") in ("me", "them") for i in items):
            mine = sum(i["amount"] for i in items if i.get("who") == "me")
            shared = sum(i["amount"] for i in items if i.get("who") not in ("me", "them"))
            ratio = (mine + shared / n) / item_sum
        else:
            ratio = 1.0 / n
    else:
        ratio = 1.0
    share = round(amount * ratio, 2)
    return {"share_gbp": share, "share_cny": round(cny * ratio, 2),
            "owed_gbp": 0.0 if settled or ratio >= 1.0 else round(amount - share, 2)}


def _row_to_expense(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["items"] = json.loads(d["items"] or "[]")
    d["settled"] = bool(d.get("settled"))
    if d.get("share_gbp") is None:
        d.update(compute_share(d))
    return d


def _sync_pantry(expense_id: int, data: dict):
    import pantry  # 延迟导入避免循环
    try:
        pantry.sync_expense(expense_id, data)
    except Exception:  # noqa: BLE001
        pass


def set_split(expense_id: int, split_kind: str, split_n: int, split_with: str, settled: bool,
              item_who: list[str] | None = None):
    e = get_expense(expense_id)
    if not e:
        return
    if item_who is not None:
        for i, who in zip(e["items"], item_who):
            i["who"] = who if who in ("me", "them", "shared") else ""
    e.update({"split_kind": split_kind, "split_n": max(1, split_n), "split_with": split_with[:100], "settled": settled})
    sh = compute_share(e)
    with _connect() as conn:
        conn.execute("""UPDATE expenses SET split_kind=?, split_n=?, split_with=?, settled=?, items=?,
                        share_gbp=?, share_cny=?, owed_gbp=?, updated_at=? WHERE id=?""",
                     (split_kind, max(1, split_n), split_with[:100], 1 if settled else 0,
                      json.dumps(e["items"], ensure_ascii=False), sh["share_gbp"], sh["share_cny"], sh["owed_gbp"],
                      int(time.time()), expense_id))
    _sync_pantry(expense_id, e)     # 标成「TA 的」的食材不进我的库存


def list_split(month: str | None = None) -> list[dict]:
    sql = "SELECT * FROM expenses WHERE split_kind != ''"
    args: list = []
    if month:
        sql += " AND date LIKE ?"
        args.append(f"{month}-%")
    sql += " ORDER BY settled ASC, date DESC, id DESC"
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_expense(r) for r in rows]


def insert_expense(data: dict) -> int:
    now = int(time.time())
    sh = compute_share(data)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO expenses (date, merchant, category, currency, amount, amount_gbp, amount_cny,
                                     fx_gbp_cny, items, note, source, image, order_ref, share_gbp, share_cny, owed_gbp,
                                     created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data["date"], data["merchant"], data["category"], data["currency"], data["amount"],
             data["amount_gbp"], data["amount_cny"], data["fx_gbp_cny"], json.dumps(data["items"], ensure_ascii=False),
             data["note"], data["source"], data.get("image", ""), data.get("order_ref", ""),
             sh["share_gbp"], sh["share_cny"], sh["owed_gbp"], now, now),
        )
        new_id = cur.lastrowid
    _sync_pantry(new_id, data)
    return new_id


def update_expense(expense_id: int, data: dict):
    # split_* 字段由 set_split 单独管，但金额/明细变了份额要重算
    old = get_expense(expense_id) or {}
    calc = {**data, "split_kind": old.get("split_kind", ""), "split_n": old.get("split_n", 1), "settled": old.get("settled", False)}
    sh = compute_share(calc)
    with _connect() as conn:
        conn.execute(
            """UPDATE expenses SET date=?, merchant=?, category=?, currency=?, amount=?, amount_gbp=?, amount_cny=?,
                                   fx_gbp_cny=?, items=?, note=?, image=?, order_ref=?, share_gbp=?, share_cny=?, owed_gbp=?,
                                   updated_at=? WHERE id=?""",
            (data["date"], data["merchant"], data["category"], data["currency"], data["amount"],
             data["amount_gbp"], data["amount_cny"], data["fx_gbp_cny"], json.dumps(data["items"], ensure_ascii=False),
             data["note"], data.get("image", ""), data.get("order_ref", ""), sh["share_gbp"], sh["share_cny"], sh["owed_gbp"],
             int(time.time()), expense_id),
        )
    _sync_pantry(expense_id, data)


def get_expense(expense_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    return _row_to_expense(row) if row else None


def delete_expense(expense_id: int) -> dict | None:
    exp = get_expense(expense_id)
    if exp:
        with _connect() as conn:
            conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
            conn.execute("DELETE FROM pantry WHERE expense_id = ?", (expense_id,))
    return exp


def list_expenses(month: str | None = None, day: str | None = None, limit: int = 0,
                  start: str | None = None, end: str | None = None) -> list[dict]:
    sql = "SELECT * FROM expenses"
    args: list = []
    if month:
        sql += " WHERE date LIKE ?"
        args.append(f"{month}-%")
    elif day:
        sql += " WHERE date = ?"
        args.append(day)
    elif start and end:
        sql += " WHERE date BETWEEN ? AND ?"
        args.extend([start, end])
    sql += " ORDER BY date DESC, id DESC"
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_expense(r) for r in rows]


def last_expense(sources: tuple[str, ...] = ()) -> dict | None:
    sql = "SELECT * FROM expenses"
    args: list = []
    if sources:
        sql += f" WHERE source IN ({','.join('?' * len(sources))})"
        args.extend(sources)
    sql += " ORDER BY id DESC LIMIT 1"
    with _connect() as conn:
        row = conn.execute(sql, args).fetchone()
    return _row_to_expense(row) if row else None


def find_by_ref(order_ref: str) -> dict | None:
    if not order_ref:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM expenses WHERE order_ref = ? ORDER BY id DESC LIMIT 1", (order_ref,)).fetchone()
    return _row_to_expense(row) if row else None


def find_same_day_amount(day: str, amount_gbp: float) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM expenses WHERE date = ? AND ABS(amount_gbp - ?) < 0.011",
                            (day, amount_gbp)).fetchall()
    return [_row_to_expense(r) for r in rows]


def find_recent_amount(before_day: str, amount_gbp: float, days: int = 14) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM expenses WHERE ABS(amount_gbp - ?) < 0.011
               AND date <= ? AND date >= date(?, ?) ORDER BY date DESC""",
            (amount_gbp, before_day, before_day, f"-{days} days")).fetchall()
    return [_row_to_expense(r) for r in rows]


def find_near_amount(d1: str, d2: str, amount_gbp: float, tol: float) -> list[dict]:
    """金额差在 tol 以内（配送费、称重差价、小费之类），按差值从小到大。"""
    with _connect() as conn:
        rows = conn.execute("""SELECT * FROM expenses WHERE ABS(amount_gbp - ?) <= ? AND date BETWEEN ? AND ?
                               ORDER BY ABS(amount_gbp - ?)""", (amount_gbp, tol, d1, d2, amount_gbp)).fetchall()
    return [_row_to_expense(r) for r in rows]


def find_amount_between(d1: str, d2: str, amount_gbp: float) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM expenses WHERE ABS(amount_gbp - ?) < 0.011 AND date BETWEEN ? AND ? ORDER BY date",
                            (amount_gbp, d1, d2)).fetchall()
    return [_row_to_expense(r) for r in rows]


def count_on_day(day: str) -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM expenses WHERE date = ?", (day,)).fetchone()[0]


def list_months() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT substr(date, 1, 7) AS month, COUNT(*) AS count,
                      SUM({SHARE_SQL}) AS total_gbp, SUM({SHARE_CNY_SQL}) AS total_cny
               FROM expenses GROUP BY month ORDER BY month DESC"""
        ).fetchall()
    return [{**dict(r), "total_gbp": round(r["total_gbp"] or 0, 2), "total_cny": round(r["total_cny"] or 0, 2)} for r in rows]


def daily_totals(start: str, end: str) -> list[dict]:
    """每天我自己那份的合计（日历年视图用，不用把整年明细都拉下来）。"""
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT date, COUNT(*) AS count, SUM({SHARE_SQL}) AS gbp, SUM({SHARE_CNY_SQL}) AS cny
               FROM expenses WHERE date BETWEEN ? AND ? GROUP BY date ORDER BY date""", (start, end)).fetchall()
    return [{**dict(r), "gbp": round(r["gbp"] or 0, 2), "cny": round(r["cny"] or 0, 2)} for r in rows]


def month_total(month: str) -> float:
    with _connect() as conn:
        v = conn.execute(f"SELECT SUM({SHARE_SQL}) FROM expenses WHERE date LIKE ?", (f"{month}-%",)).fetchone()[0]
    return float(v or 0)


def month_summary(month: str) -> dict:
    """按分类 / 按天 / 按商家汇总。有明细的按明细分类摊（明细合计和总额对不上
    时按比例缩放），没明细的整笔记到主分类。"""
    expenses = list_expenses(month=month)
    by_cat_gbp: dict[str, float] = defaultdict(float)
    by_cat_cny: dict[str, float] = defaultdict(float)
    by_sub: dict[tuple[str, str], float] = defaultdict(float)
    by_day: dict[str, float] = defaultdict(float)
    by_merchant: dict[str, float] = defaultdict(float)
    total_gbp = total_cny = gross_gbp = owed_gbp = 0.0
    for e in expenses:
        gross_gbp += e["amount_gbp"]
        owed_gbp += e["owed_gbp"]
        share_gbp, share_cny = e["share_gbp"], e["share_cny"]     # 只算我自己那份
        total_gbp += share_gbp
        total_cny += share_cny
        if not share_gbp:
            continue
        by_day[e["date"]] += share_gbp
        by_merchant[e["merchant"] or "（未填商家）"] += share_gbp
        items = [i for i in e["items"] if (i.get("amount") or 0) > 0]
        item_sum = sum(i["amount"] for i in items)
        if items and item_sum > 0 and e["amount"] > 0:
            # 每项算我的份额：me 全额，them 0，shared 1/n（非分账全是 1），再按比例摊到我的实付份额
            n = max(int(e.get("split_n") or 1), 1) if e.get("split_kind") == "split" else 1
            weights = [i["amount"] * (1.0 if i.get("who") == "me" else 0.0 if i.get("who") == "them" else 1.0 / n) for i in items]
            wsum = sum(weights) or 1.0
            for i, w in zip(items, weights):
                key = coerce_category(i.get("category") or e["category"])
                by_cat_gbp[key] += share_gbp * w / wsum
                by_cat_cny[key] += share_cny * w / wsum
                by_sub[(key, coerce_sub(key, i.get("sub")))] += share_gbp * w / wsum
        else:
            by_cat_gbp[e["category"]] += share_gbp
            by_cat_cny[e["category"]] += share_cny
            by_sub[(e["category"], "")] += share_gbp

    def _subs(cat: str) -> list[dict]:
        rows = [{"key": sub, "label": SUB_LABEL.get((cat, sub), "未细分"), "gbp": round(v, 2)}
                for (c, sub), v in by_sub.items() if c == cat and v > 0]
        return sorted(rows, key=lambda r: -r["gbp"])

    categories = [
        {"key": c["key"], "label": c["label"], "emoji": c["emoji"],
         "gbp": round(by_cat_gbp[c["key"]], 2), "cny": round(by_cat_cny[c["key"]], 2),
         "pct": round(by_cat_gbp[c["key"]] / total_gbp * 100, 1) if total_gbp else 0,
         "subs": _subs(c["key"])}
        for c in CATEGORIES if by_cat_gbp[c["key"]] > 0
    ]
    categories.sort(key=lambda c: -c["gbp"])
    merchants = sorted(({"name": k, "gbp": round(v, 2)} for k, v in by_merchant.items()), key=lambda m: -m["gbp"])[:8]
    return {
        "month": month,
        "count": len(expenses),
        "total_gbp": round(total_gbp, 2),
        "total_cny": round(total_cny, 2),
        "gross_gbp": round(gross_gbp, 2),       # 实际刷卡总额（含代付和别人那份）
        "owed_gbp": round(owed_gbp, 2),         # 还没收回来的
        "days_logged": len(by_day),
        "by_category": categories,
        "by_day": [{"date": d, "gbp": round(v, 2)} for d, v in sorted(by_day.items())],
        "top_merchants": merchants,
    }


# ---- inbound emails ----------------------------------------------------

def inbound_seen(message_id: str) -> bool:
    if not message_id:
        return False
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM inbound_emails WHERE message_id = ?", (message_id,)).fetchone() is not None


def inbound_ref_seen(order_ref: str) -> bool:
    """同一订单号已经入过账（Deliveroo 一单会发确认 + 送达好几封）。"""
    if not order_ref:
        return False
    with _connect() as conn:
        hit = conn.execute("SELECT 1 FROM inbound_emails WHERE order_ref = ? AND status = 'ok'", (order_ref,)).fetchone()
        if hit:
            return True
        # 截图先到、邮件后到：expenses 里已经有这个订单号
        return conn.execute("SELECT 1 FROM expenses WHERE order_ref = ?", (order_ref,)).fetchone() is not None


def insert_inbound(message_id: str, subject: str, sender: str, order_ref: str, status: str,
                   detail: str = "", expense_id: int = 0):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO inbound_emails (message_id, subject, sender, order_ref, status, detail, expense_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, subject[:200], sender[:120], order_ref[:80], status, detail[:300], expense_id, int(time.time())))


def list_inbound(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM inbound_emails ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---- pantry --------------------------------------------------------------

def upsert_pantry(expense_id: int, idx: int, name: str, name_raw: str, sub: str, qty: float, bought: str, expires: str):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO pantry (expense_id, item_idx, name, name_raw, sub, qty, bought, expires, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'in', ?)
               ON CONFLICT(expense_id, item_idx) DO UPDATE SET
                 name=excluded.name, name_raw=excluded.name_raw, sub=excluded.sub, qty=excluded.qty,
                 bought=excluded.bought, expires=excluded.expires""",
            (expense_id, idx, name[:60], name_raw[:80], sub, qty, bought, expires, int(time.time())))


def prune_pantry(expense_id: int, keep_idx: list[int]):
    with _connect() as conn:
        if keep_idx:
            conn.execute(f"DELETE FROM pantry WHERE expense_id = ? AND item_idx NOT IN ({','.join('?' * len(keep_idx))})",
                         (expense_id, *keep_idx))
        else:
            conn.execute("DELETE FROM pantry WHERE expense_id = ?", (expense_id,))


def list_pantry(status: str = "in") -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM pantry WHERE status = ? ORDER BY expires, id", (status,)).fetchall()
    return [dict(r) for r in rows]


def update_items(expense_id: int, items: list[dict]):
    """只改明细（补全中文名/子类/保质期），金额不动。"""
    e = get_expense(expense_id)
    if not e:
        return
    with _connect() as conn:
        conn.execute("UPDATE expenses SET items = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(items, ensure_ascii=False), int(time.time()), expense_id))
    _sync_pantry(expense_id, {**e, "items": items})


def set_pantry_status(pantry_id: int, status: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("UPDATE pantry SET status = ?, updated_at = ? WHERE id = ?", (status, int(time.time()), pantry_id))
        return cur.rowcount > 0


def set_pantry_expires(pantry_id: int, expires: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("UPDATE pantry SET expires = ?, updated_at = ? WHERE id = ?", (expires, int(time.time()), pantry_id))
        return cur.rowcount > 0
