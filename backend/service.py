"""app.py 和 telegram.py 共用的业务逻辑：保存一笔消费（算汇率）、生成文案。"""
import re
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import database
import fx
from categories import CATEGORY_MAP, coerce_category, coerce_sub
from config import HOME_CURRENCY, RECEIPTS_DIR, REPORT_CURRENCY, SITE_URL, TIMEZONE

TZ = ZoneInfo(TIMEZONE)


def today() -> date:
    return datetime.now(TZ).date()


def now_local() -> datetime:
    return datetime.now(TZ)


def build_expense(payload: dict, source: str = "manual", image: str = "") -> dict:
    """前端/Telegram 传来的字段 → 可入库的完整记录（含汇率换算）。"""
    day = str(payload.get("date") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        day = today().isoformat()
    currency = str(payload.get("currency") or HOME_CURRENCY).upper()[:3]
    amount = round(abs(float(payload.get("amount") or 0)), 2)
    if amount <= 0:
        raise ValueError("金额得大于 0")
    to_gbp = fx.get_rate(day, currency, HOME_CURRENCY)
    gbp_cny = fx.get_rate(day, HOME_CURRENCY, REPORT_CURRENCY)
    amount_gbp = round(amount * to_gbp, 2)
    items = []
    for raw in payload.get("items") or []:
        name = str(raw.get("name") or "").strip()[:80]
        if not name:
            continue
        icat = coerce_category(raw.get("category") or payload.get("category"))
        items.append({
            "name": name,
            "name_zh": str(raw.get("name_zh") or "").strip()[:40],
            "shelf_days": int(max(0, min(730, float(raw.get("shelf_days") or 0)))),
            "qty": float(raw.get("qty") or 1),
            "amount": round(abs(float(raw.get("amount") or 0)), 2),
            "category": icat,
            "sub": coerce_sub(icat, raw.get("sub")),
            "who": raw.get("who") if raw.get("who") in ("me", "them", "shared") else "",
        })
    return {
        "date": day,
        "merchant": str(payload.get("merchant") or "").strip()[:60],
        "category": coerce_category(payload.get("category")),
        "currency": currency,
        "amount": amount,
        "amount_gbp": amount_gbp,
        "amount_cny": round(amount_gbp * gbp_cny, 2),
        "fx_gbp_cny": gbp_cny,
        "items": items,
        "note": str(payload.get("note") or "").strip()[:300],
        "source": source,
        "image": image,
        "order_ref": str(payload.get("order_ref") or "").strip()[:80],
    }


def find_duplicate(record: dict, date_known: bool = True) -> dict | None:
    """这笔是不是已经记过了（邮件 + 截图 / 小票 + 支付截图）：
    1. 订单号一样
    2. 同一天同金额且商家像
    3. 截图上没日期（date 是默认的今天）时：14 天内同金额、商家像、且已有那笔还没填单项价格
       ——这就是「邮件先到、截图补明细」的场景；已有那笔有价格的话不合并，避免把两顿一样的饭并掉"""
    hit = database.find_by_ref(record.get("order_ref", ""))
    if hit:
        return hit
    mine = _tokens(record["merchant"])
    for cand in database.find_same_day_amount(record["date"], record["amount_gbp"]):
        theirs = _tokens(cand["merchant"])
        if not mine or not theirs or mine & theirs:
            return cand
    if not date_known:
        for cand in database.find_recent_amount(record["date"], record["amount_gbp"]):
            if items_unpriced(cand) and mine & _tokens(cand["merchant"]):
                return cand
    # 4. 短信补的占位记录（没明细）：银行扣款日常比下单晚一两天，±3 天同金额同商家就是它
    day = date.fromisoformat(record["date"])
    for cand in database.find_amount_between((day - timedelta(days=3)).isoformat(), (day + timedelta(days=3)).isoformat(),
                                             record["amount_gbp"]):
        if not cand["items"] and cand.get("source") == "sms" and mine & _tokens(cand["merchant"]):
            return cand
    return None


def merge_into(existing: dict, record: dict) -> dict:
    """把新识别的结果并进已有记录：明细以新的为准（旧的没明细价格才需要补），
    金额只在新的有值时覆盖，备注和图片都留着。"""
    merged = dict(existing)
    if record["items"]:
        old = existing["items"]
        if items_unpriced(existing) and len(old) == len(record["items"]):
            # 邮件给的名字准（截图上名字常被截断/靠图猜），价格用截图的，按顺序对上
            merged["items"] = [{"name": o["name"], "qty": n.get("qty") or o.get("qty") or 1,
                                "amount": n["amount"], "category": o["category"]}
                               for o, n in zip(old, record["items"])]
        else:
            merged["items"] = record["items"]
    if record["amount"] > 0 and abs(record["amount_gbp"] - existing["amount_gbp"]) > 0.011:
        merged.update({k: record[k] for k in ("amount", "amount_gbp", "amount_cny", "fx_gbp_cny", "currency")})
    if record["category"] != "other":
        merged["category"] = record["category"]
    merged["merchant"] = merged["merchant"] or record["merchant"]
    if existing.get("source") == "sms" and not existing["items"]:
        merged["date"] = record["date"]                      # 短信占位 → 换成真正的下单日期
        merged["merchant"] = record["merchant"] or merged["merchant"]
        merged["category"] = record["category"]
    merged["order_ref"] = merged["order_ref"] or record.get("order_ref", "")
    merged["image"] = merged["image"] or record.get("image", "")
    notes = [n for n in (existing["note"], record["note"]) if n and n not in existing["note"]]
    merged["note"] = " · ".join(dict.fromkeys(notes))[:300] if notes else existing["note"]
    database.update_expense(existing["id"], merged)
    return database.get_expense(existing["id"])


# ---- bank SMS reconcile --------------------------------------------------

def reconcile(transactions: list[dict]) -> list[dict]:
    """每条短信交易 → matched（找到同金额、日期 ±3 天、商家像的记录）/ maybe（同金额同期
    但商家对不上）/ unmatched。一笔账本记录只能对一条短信。"""
    used: set[int] = set()
    results = []
    for tx in transactions:
        res = {"tx": tx, "status": "unmatched", "expense": None}
        if tx.get("kind") != "purchase":
            res["status"] = "ignored"
            results.append(res)
            continue
        day = date.fromisoformat(tx["date"])
        amount_gbp = round(tx["amount"] * fx.get_rate(tx["date"], tx["currency"], HOME_CURRENCY), 2)
        cands = [c for c in database.find_amount_between((day - timedelta(days=3)).isoformat(),
                                                          (day + timedelta(days=3)).isoformat(), amount_gbp)
                 if c["id"] not in used]
        mine = _tokens(tx["merchant"])
        strong = [c for c in cands if mine & _tokens(c["merchant"])]
        pick = None
        if strong:
            pick = min(strong, key=lambda c: abs(date.fromisoformat(c["date"]) - day))
            res["status"] = "matched"
        elif cands:
            pick = min(cands, key=lambda c: abs(date.fromisoformat(c["date"]) - day))
            res["status"] = "maybe"
        else:
            # 金额差一点点但商家对得上：多半是配送费 / 称重差价 / 小费后来才加的（Ocado、Deliveroo 常见）
            tol = max(5.0, amount_gbp * 0.15)
            near = [c for c in database.find_near_amount((day - timedelta(days=3)).isoformat(),
                                                        (day + timedelta(days=3)).isoformat(), amount_gbp, tol)
                    if c["id"] not in used and mine and mine & _tokens(c["merchant"])]
            if near:
                pick = near[0]
                res["status"] = "adjust"
                res["diff_gbp"] = round(amount_gbp - pick["amount_gbp"], 2)
        if pick:
            used.add(pick["id"])
            res["expense"] = {"id": pick["id"], "date": pick["date"], "merchant": pick["merchant"],
                              "amount_gbp": pick["amount_gbp"], "category": pick["category"]}
        results.append(res)
    return results


def apply_adjust(expense_id: int, tx: dict) -> dict | None:
    """把账本金额改成短信金额；多出来的差额记成一行「配送费/差价」，少了就只改金额。"""
    e = database.get_expense(expense_id)
    if not e:
        return None
    new_amount = round(tx["amount"] * fx.get_rate(tx["date"], tx["currency"], e["currency"]), 2) if tx["currency"] != e["currency"] else round(tx["amount"], 2)
    diff = round(new_amount - e["amount"], 2)
    if abs(diff) < 0.005:
        return e
    items = list(e["items"])
    if diff > 0:
        sub = "fees" if coerce_sub(e["category"], "fees") else ""
        items.append({"name": "配送费 / 差价（按银行短信补）", "qty": 1, "amount": diff, "category": e["category"], "sub": sub, "who": ""})
    note = (e["note"] + " · " if e["note"] else "") + f"银行扣款 {e['currency']} {new_amount:.2f}（原记 {e['amount']:.2f}）"
    record = build_expense({**e, "amount": new_amount, "items": items, "note": note[:300]}, source=e["source"], image=e["image"])
    database.update_expense(expense_id, record)
    return database.get_expense(expense_id)


def record_sms(tx: dict, classify) -> dict:
    """没对上的短信交易按短信记一笔（source=sms，没明细），之后小票来了会自动并进去。"""
    guess = classify(tx["merchant"]) if tx.get("merchant") else {"merchant": "", "category": "other", "sub": "", "note": ""}
    record = build_expense({
        "date": tx["date"], "merchant": guess["merchant"] or tx["merchant"], "category": guess["category"],
        "currency": tx["currency"], "amount": tx["amount"], "items": [],
        "note": f"信用卡短信：{tx['merchant']}".strip("：") if tx.get("merchant") else "信用卡短信",
    }, source="sms")
    record["id"] = database.insert_expense(record)
    return record


def items_unpriced(record: dict) -> bool:
    return bool(record["items"]) and not any(i.get("amount") for i in record["items"])


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9\u4e00-\u9fff]+", (name or "").lower()) if len(t) > 1}


def save_image(data: bytes, mime: str) -> str:
    ext = {"image/png": "png", "image/webp": "webp", "image/heic": "heic"}.get(mime, "jpg")
    name = f"{today().isoformat()}-{uuid.uuid4().hex[:8]}.{ext}"
    (RECEIPTS_DIR / name).write_bytes(data)
    return name


def delete_image(name: str):
    if name and re.fullmatch(r"[\w.-]+", name):
        p = RECEIPTS_DIR / name
        if p.exists():
            p.unlink()


# ---- text for Telegram --------------------------------------------------

def money(gbp: float, cny: float | None = None) -> str:
    s = f"£{gbp:,.2f}"
    if cny is not None:
        s += f"（≈ ¥{cny:,.0f}）"
    return s


def expense_text(e: dict) -> str:
    cat = CATEGORY_MAP[e["category"]]
    head = f"{cat['emoji']} <b>{_esc(e['merchant'] or cat['label'])}</b>  {money(e['amount_gbp'], e['amount_cny'])}"
    if e["currency"] != HOME_CURRENCY:
        head += f"  <i>原价 {e['currency']} {e['amount']:.2f}</i>"
    lines = [head, f"{e['date']} · {cat['label']}"]
    if e.get("split_kind") == "split":
        shared = [i for i in e["items"] if i.get("who") not in ("me", "them")]
        detail = f"，公用 {len(shared)} 项" if any(i.get("who") in ("me", "them") for i in e["items"]) else ""
        lines.append(f"👥 {e['split_n']} 人分{detail}，我 £{e['share_gbp']:.2f}" + (f"，和 {_esc(e['split_with'])}" if e.get("split_with") else "")
                     + ("，已收齐" if e.get("settled") else f"，待收 £{e['owed_gbp']:.2f}"))
    elif e.get("split_kind") == "paid_for":
        lines.append("🤝 代付" + (f"给 {_esc(e['split_with'])}" if e.get("split_with") else "") + ("，已收回" if e.get("settled") else f"，待收 £{e['owed_gbp']:.2f}"))
    for i in e["items"][:12]:
        icat = CATEGORY_MAP[coerce_category(i.get("category"))]["emoji"]
        qty = f" ×{i['qty']:g}" if i.get("qty", 1) != 1 else ""
        amt = f"  {i['amount']:.2f}" if i.get("amount") else ""     # 没单项价格就只列名字
        shown = f"{i['name_zh']} · {i['name']}" if i.get("name_zh") and i["name_zh"] != i["name"] else i["name"]
        lines.append(f"  {icat} {_esc(shown)}{qty}{amt}")
    if len(e["items"]) > 12:
        lines.append(f"  … 还有 {len(e['items']) - 12} 项")
    if e["note"]:
        lines.append(f"📝 {_esc(e['note'])}")
    lines.append(f'<a href="{SITE_URL}/expense/{e["id"]}">改一下</a> · /undo 撤销')
    return "\n".join(lines)


def month_report_text(month: str) -> str:
    s = database.month_summary(month)
    y, m = month.split("-")
    if not s["count"]:
        return f"📒 {y}年{int(m)}月还没有任何记录。"
    prev = _prev_month(month)
    prev_total = database.month_total(prev)
    lines = [f"📒 <b>{y}年{int(m)}月汇总</b>", f"总支出 {money(s['total_gbp'], s['total_cny'])}"]
    if prev_total:
        diff = (s["total_gbp"] - prev_total) / prev_total * 100
        lines.append(f"比上月 {'+' if diff >= 0 else ''}{diff:.0f}%（上月 £{prev_total:,.2f}）")
    budget = database.get_setting("budget_gbp")
    if budget:
        try:
            b = float(budget)
            lines.append(f"预算 £{b:,.0f}，{'超了' if s['total_gbp'] > b else '还剩'} £{abs(b - s['total_gbp']):,.2f}")
        except ValueError:
            pass
    lines.append("")
    for c in s["by_category"]:
        lines.append(f"{c['emoji']} {c['label']}  £{c['gbp']:,.2f}  ({c['pct']:.0f}%)")
    lines.append("")
    lines.append(f"记了 {s['count']} 笔，{s['days_logged']} 天有记录")
    if s["top_merchants"]:
        top = "、".join(f"{m['name']} £{m['gbp']:,.0f}" for m in s["top_merchants"][:3])
        lines.append(f"花最多：{_esc(top)}")
    lines.append(f'<a href="{SITE_URL}/?m={month}">看详情</a>')
    return "\n".join(lines)


def _prev_month(month: str) -> str:
    y, m = map(int, month.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def status() -> dict:
    t = today()
    last = database.last_expense()
    last_date = date.fromisoformat(last["date"]) if last else None
    return {
        "today": t.isoformat(),
        "today_logged": database.count_on_day(t.isoformat()) > 0,
        "last_entry_date": last["date"] if last else "",
        "days_since_last": (t - last_date).days if last_date else -1,
        "telegram_bound": bool(database.get_setting("telegram_chat_id")),
    }


def days_in_month(month: str) -> int:
    y, m = map(int, month.split("-"))
    first_next = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return (first_next - date(y, m, 1)).days


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


__all__ = ["build_expense", "find_duplicate", "merge_into", "items_unpriced", "reconcile", "record_sms", "apply_adjust", "save_image", "delete_image", "expense_text", "month_report_text",
           "status", "today", "now_local", "days_in_month", "timedelta", "TZ"]
