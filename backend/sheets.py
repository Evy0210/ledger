"""合租分账表（Google Sheet）双向同步。

表只有两个 tab：「明细」是三个人唯一的录入口，室友直接在上面加行；「汇总」全是公式。
这边只碰「明细」，列固定 A-L（见 COL_*）：

- push：本站**和室友**分的账单（和别的朋友分的不推），按隐藏的 ID 列 upsert 成一行
  （室友手写的行没 ID，永远不碰）。金额只推**公用部分**——
  商品级标成「我的 / TA 的」的东西不进合租账，室友不用理解任何特殊规则。
- pull：室友在表上勾的「已结清」写回本地库。

「已结清」两边都能改，用三方合并定谁赢：和上次同步时的值比，谁变了听谁的；
都变了听表的（室友刚勾完的可能性更大）。上次的值存在 settings.sheets_last_settled。

没配 SHEETS_ID / SHEETS_SA_JSON 时整个模块静默关闭。
"""
import json
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote

import httpx

import database
from config import SHEETS_ID, SHEETS_ME, SHEETS_PEOPLE, SHEETS_SA_JSON, SHEETS_TAB

log = logging.getLogger("ledger.sheets")

API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
FIRST_ROW, LAST_ROW, NCOL = 2, 500, 12
COL_DATE, COL_MERCHANT, COL_AMOUNT, COL_PAYER = 0, 1, 2, 3
COL_P0, COL_N, COL_EACH = 4, 7, 8
COL_NOTE, COL_SETTLED, COL_ID = 9, 10, 11
LAST_KEY = "sheets_last_settled"

_token = {"value": "", "exp": 0.0}
_lock = threading.Lock()


def enabled() -> bool:
    return bool(SHEETS_ID and SHEETS_SA_JSON and SHEETS_ME in SHEETS_PEOPLE)


def roster() -> dict:
    """花名册。前端用它把「和谁分」做成标签选择，不用手打名字。不读表，很便宜。"""
    return {"enabled": enabled(), "me": SHEETS_ME, "people": SHEETS_PEOPLE,
            "roommates": [p for p in SHEETS_PEOPLE if p != SHEETS_ME]}


def _sa_info() -> dict:
    raw = SHEETS_SA_JSON.strip()
    return json.loads(raw) if raw.startswith("{") else json.loads(Path(raw).read_text("utf-8"))


def _access_token() -> str:
    """服务账号 JWT 换 access token。只用 google-auth 签名，HTTP 还是走 httpx。"""
    with _lock:
        if _token["value"] and time.time() < _token["exp"] - 60:
            return _token["value"]
        from google.auth import crypt, jwt
        info = _sa_info()
        now = int(time.time())
        assertion = jwt.encode(crypt.RSASigner.from_service_account_info(info), {
            "iss": info["client_email"], "scope": SCOPE,
            "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 3600})
        r = httpx.post("https://oauth2.googleapis.com/token", timeout=20, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion.decode() if isinstance(assertion, bytes) else assertion})
        r.raise_for_status()
        d = r.json()
        _token.update(value=d["access_token"], exp=time.time() + int(d.get("expires_in", 3600)))
        return _token["value"]


def _req(method: str, path: str, **kw) -> dict:
    r = httpx.request(method, f"{API}/{SHEETS_ID}{path}", timeout=30,
                      headers={"Authorization": f"Bearer {_access_token()}"}, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def _read_rows() -> list[list]:
    rng = quote(f"{SHEETS_TAB}!A{FIRST_ROW}:L{LAST_ROW}", safe="")
    # 日期要 FORMATTED_STRING：默认给的是序列号（46275），和本地的 "2026-09-10" 永远不等，
    # 会害得每轮同步都把所有行重写一遍。金额仍然按 UNFORMATTED 拿原始数字。
    d = _req("GET", f"/values/{rng}", params={"valueRenderOption": "UNFORMATTED_VALUE",
                                              "dateTimeRenderOption": "FORMATTED_STRING"})
    return d.get("values", [])


def _tab_id() -> int:
    d = _req("GET", "", params={"fields": "sheets.properties(sheetId,title)"})
    for s in d.get("sheets", []):
        if s["properties"]["title"] == SHEETS_TAB:
            return s["properties"]["sheetId"]
    raise RuntimeError(f"表里没有「{SHEETS_TAB}」这个 tab")


def _cell(row: list, i: int):
    return row[i] if len(row) > i else ""


def _truthy(v) -> bool:
    return v is True or str(v).strip().upper() in ("TRUE", "是", "1")


# ---- 本地账单 → 表格里的行 ------------------------------------------------

def _names(e: dict) -> list[str]:
    return [x for x in re.split(r"[,，、/\s]+", e.get("split_with") or "") if x]


def _participants(e: dict) -> list[str]:
    """谁参与分摊。从「和谁分」里按花名册认名字；代付不算我自己。"""
    text = (e.get("split_with") or "").lower()
    others = [p for p in SHEETS_PEOPLE if p.lower() != SHEETS_ME.lower() and p.lower() in text]
    return others if e.get("split_kind") == "paid_for" else [SHEETS_ME] + others


def _item_text(items: list[dict], limit: int = 110) -> str:
    """明细名字连成一串，给室友看「这行到底是些什么东西」。优先中文名。"""
    names = [n for n in ((i.get("name_zh") or i.get("name") or "").strip() for i in items) if n]
    if not names:
        return ""
    out = "、".join(names)
    if len(out) <= limit:
        return out
    kept: list[str] = []
    for n in names:
        if len("、".join(kept + [n])) > limit:
            break
        kept.append(n)
    return "、".join(kept or names[:1]) + f" 等 {len(names)} 项"


def _buckets(e: dict) -> tuple[tuple[float, list], tuple[float, list]]:
    """把一笔拆成（公用：金额+明细）和（「TA 的」：金额+明细），金额折成英镑。
    没做商品级标注就整单算公用。"""
    items = [i for i in (e.get("items") or []) if (i.get("amount") or 0) > 0]
    total = sum(i["amount"] for i in items)
    amt = float(e["amount_gbp"])
    if e.get("split_kind") != "split" or total <= 0 or not any(i.get("who") in ("me", "them") for i in items):
        return (round(amt, 2), items), (0.0, [])
    shared_items = [i for i in items if i.get("who") not in ("me", "them")]
    them_items = [i for i in items if i.get("who") == "them"]
    return ((round(amt * sum(i["amount"] for i in shared_items) / total, 2), shared_items),
            (round(amt * sum(i["amount"] for i in them_items) / total, 2), them_items))


def sheet_rows(e: dict) -> list[tuple[str, float, list[str], str]]:
    """一笔账在合租表里占哪几行 —— (ID 后缀, 金额, 参与人, 备注)。

    最多两行，因为这两部分的分摊人不一样：
    - 公用的：我和室友一起平摊
    - 「TA 的」：我垫的、只归别人的东西，只摊给室友（我不分担）
    备注放这行具体是些什么东西（矿泉水、晾衣架…），室友一眼能对上。
    金额都按人头比例缩放：和 4 个人分但只有 1 个是室友时，只记属于合租的那几份。
    不涉及室友、或金额凑不满 1 便士的，返回空 —— 不进合租表。
    """
    others = [p for p in _participants(e) if p != SHEETS_ME]
    if not others:
        return []
    (shared, shared_items), (theirs, them_items) = _buckets(e)
    out: list[tuple[str, float, list[str], str]] = []

    if e.get("split_kind") == "paid_for":
        amt = round(shared * len(others) / max(len(_names(e)), 1), 2)
        return [("", amt, others, _item_text(shared_items))] if amt >= 0.01 else []

    heads = max(int(e.get("split_n") or 1), 1)
    amt = round(shared * (1 + len(others)) / heads, 2)
    if amt >= 0.01:
        note = _item_text(shared_items)
        if heads != 1 + len(others):
            note = (note + f"（公用共 £{shared:.2f} 由 {heads} 人分，这里只记 {1 + len(others)} 份）").strip()
        out.append(("", amt, [SHEETS_ME] + others, note))
    if theirs >= 0.01 and heads > 1:
        amt_t = round(theirs * len(others) / (heads - 1), 2)
        if amt_t >= 0.01:
            note = _item_text(them_items)
            if heads - 1 != len(others):
                note = (note + f"（共 £{theirs:.2f} 归 {heads - 1} 个人，这里只记室友那 {len(others)} 份）").strip()
            out.append(("t", amt_t, others, note))
    return out


def _row_values(e: dict, row_no: int, amount: float, part: list[str], extra: str, suffix: str) -> list:
    note = extra          # 就放这行的物品；本地 note 是给自己看的，不进合租表
    v = [""] * NCOL
    v[COL_DATE] = e["date"]
    v[COL_MERCHANT] = e.get("merchant") or "（没写商家）"
    v[COL_AMOUNT] = amount
    v[COL_PAYER] = SHEETS_ME
    for i, p in enumerate(SHEETS_PEOPLE[:3]):
        v[COL_P0 + i] = p in part
    v[COL_N] = f'=IF($A{row_no}="","",N($E{row_no}=TRUE)+N($F{row_no}=TRUE)+N($G{row_no}=TRUE))'
    v[COL_EACH] = f'=IF(OR($A{row_no}="",$H{row_no}=0),"",ROUND($C{row_no}/$H{row_no},2))'
    v[COL_NOTE] = note
    v[COL_SETTLED] = bool(e.get("settled"))
    v[COL_ID] = f"e{e['id']}{suffix}"
    return v


# ---- 同步 ----------------------------------------------------------------

def sync() -> dict:
    """跑一轮双向同步，返回这次改了些什么。没开启就直接返回。"""
    if not enabled():
        return {"enabled": False}

    rows = _read_rows()
    in_sheet: dict[str, tuple[int, list]] = {}
    used_rows: set[int] = set()
    for i, r in enumerate(rows):
        row_no = i + FIRST_ROW
        if str(_cell(r, COL_DATE)).strip():
            used_rows.add(row_no)
        rid = str(_cell(r, COL_ID)).strip()
        if rid:
            in_sheet[rid] = (row_no, r)

    # 一笔账可能占两行（公用的 + 「TA 的」），所以 ID 带后缀；rid -> (账单, 金额, 参与人, 附注)
    mine: dict[str, tuple] = {}
    for e in database.list_split():
        if e.get("split_kind") not in ("split", "paid_for"):
            continue
        for suffix, amount, part, extra in sheet_rows(e):
            mine[f"e{e['id']}{suffix}"] = (e, amount, part, extra, suffix)
    last: dict = json.loads(database.get_setting(LAST_KEY) or "{}")

    # 1) 「已结清」三方合并
    pulled = []
    for rid, (e, *_rest) in mine.items():
        if rid not in in_sheet:
            continue
        sheet_v = _truthy(_cell(in_sheet[rid][1], COL_SETTLED))
        db_v, last_v = bool(e["settled"]), last.get(rid)
        if sheet_v != db_v and (last_v is None or sheet_v != last_v):
            database.set_split(e["id"], e["split_kind"], e["split_n"], e["split_with"], sheet_v)
            e["settled"] = sheet_v          # 同一笔的另一行共享这个 dict，会一起跟着变
            pulled.append(rid)

    # 2) 本地 → 表格：改过的更新，没有的追加
    updates, appended = [], []
    next_row = max(used_rows) + 1 if used_rows else FIRST_ROW
    for rid, (e, amount, part, extra, suffix) in sorted(
            mine.items(), key=lambda kv: (kv[1][0]["date"], kv[1][0]["id"], kv[1][4])):
        if rid in in_sheet:
            row_no, old = in_sheet[rid]
        else:
            if next_row > LAST_ROW:
                log.warning("明细表满了（%d 行），剩下的没推", LAST_ROW)
                break
            row_no, old = next_row, []
            next_row += 1
            appended.append(rid)
        new = _row_values(e, row_no, amount, part, extra, suffix)
        if _changed(old, new):
            updates.append((row_no, new))

    # 3) 本站删掉 / 取消分账的，把表里对应的行删掉（只动有 ID 的行）
    # 有 ID 的行都是本站推上去的；室友手写的行没有 ID，永远不碰
    stale = [(row_no, r) for rid, (row_no, r) in in_sheet.items() if rid not in mine]
    if updates:
        _req("POST", "/values:batchUpdate", json={
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": f"'{SHEETS_TAB}'!A{n}:L{n}", "values": [v]} for n, v in updates]})
    if stale:
        tab = _tab_id()
        _req("POST", ":batchUpdate", json={"requests": [
            {"deleteDimension": {"range": {"sheetId": tab, "dimension": "ROWS",
                                           "startIndex": n - 1, "endIndex": n}}}
            for n, _ in sorted(stale, reverse=True)]})

    database.set_setting(LAST_KEY, json.dumps({rid: bool(v[0]["settled"]) for rid, v in mine.items()}))
    out = {"enabled": True, "pushed": len(updates), "appended": len(appended),
           "pulled": len(pulled), "removed": len(stale)}
    log.info("sheet sync %s", out)
    return out


def _changed(old: list, new: list) -> bool:
    """只比人看得见的列；H/I 是公式，读回来是算好的数字，比了会一直不相等。"""
    cols = [COL_DATE, COL_MERCHANT, COL_AMOUNT, COL_PAYER, COL_P0, COL_P0 + 1, COL_P0 + 2,
            COL_NOTE, COL_SETTLED, COL_ID]
    for c in cols:
        a, b = _cell(old, c), new[c]
        if isinstance(b, bool):
            if _truthy(a) != b:
                return True
        elif isinstance(b, float):
            if abs(float(a or 0) - b) > 0.005:
                return True
        elif str(a).strip() != str(b).strip():
            return True
    return False


# ---- 给前端看的：整张表 + 每人净额 ----------------------------------------

def overview() -> dict:
    """读「明细」算出和汇总 tab 一样的数字，这样网页上不用开表就能看。"""
    if not enabled():
        return {"enabled": False, "people": [], "rows": []}
    rows, out = _read_rows(), []
    paid = {p: 0.0 for p in SHEETS_PEOPLE}
    owes = {p: 0.0 for p in SHEETS_PEOPLE}
    for i, r in enumerate(rows):
        if not str(_cell(r, COL_DATE)).strip():
            continue
        amount = float(_cell(r, COL_AMOUNT) or 0)
        part = [p for j, p in enumerate(SHEETS_PEOPLE[:3]) if _truthy(_cell(r, COL_P0 + j))]
        settled = _truthy(_cell(r, COL_SETTLED))
        payer = str(_cell(r, COL_PAYER)).strip()
        each = round(amount / len(part), 2) if part else 0.0
        if not settled:
            if payer in paid:
                paid[payer] += amount
            for p in part:
                owes[p] += each
        out.append({"row": i + FIRST_ROW, "date": str(_cell(r, COL_DATE)),
                    "merchant": str(_cell(r, COL_MERCHANT)), "amount_gbp": round(amount, 2),
                    "payer": payer, "participants": part, "each_gbp": each,
                    "note": str(_cell(r, COL_NOTE)),
                    "settled": settled, "ref": str(_cell(r, COL_ID)),
                    "from_web": bool(str(_cell(r, COL_ID)).strip())})
    people = [{"name": p, "paid_gbp": round(paid[p], 2), "owes_gbp": round(owes[p], 2),
               "net_gbp": round(paid[p] - owes[p], 2)} for p in SHEETS_PEOPLE]
    return {"enabled": True, "me": SHEETS_ME, "people": people,
            "url": f"https://docs.google.com/spreadsheets/d/{SHEETS_ID}/edit",
            "transfers": _settle(people), "rows": out,
            "open_count": sum(1 for r in out if not r["settled"]),
            "open_gbp": round(sum(r["amount_gbp"] for r in out if not r["settled"]), 2)}


def _settle(people: list[dict]) -> list[dict]:
    """最少转账次数的结清方案（贪心：最大债主还给最大债权人）。"""
    debt = sorted([p.copy() for p in people if p["net_gbp"] < -0.005], key=lambda x: x["net_gbp"])
    cred = sorted([p.copy() for p in people if p["net_gbp"] > 0.005], key=lambda x: -x["net_gbp"])
    out = []
    i = j = 0
    while i < len(debt) and j < len(cred):
        amt = round(min(-debt[i]["net_gbp"], cred[j]["net_gbp"]), 2)
        if amt > 0.005:
            out.append({"from": debt[i]["name"], "to": cred[j]["name"], "amount_gbp": amt})
        debt[i]["net_gbp"] += amt
        cred[j]["net_gbp"] -= amt
        if debt[i]["net_gbp"] > -0.005:
            i += 1
        if cred[j]["net_gbp"] < 0.005:
            j += 1
    return out


async def run():
    """后台定时同步。和 telegram / mail 一样，在 app 的 lifespan 里起一个 task。"""
    import asyncio
    from config import SHEETS_SYNC_MINUTES
    if not enabled():
        log.info("没配 SHEETS_ID / SHEETS_SA_JSON，合租分账表同步关闭")
        return
    await asyncio.sleep(10)          # 等 app 起来
    while True:
        try:
            await asyncio.to_thread(sync)
        except Exception as e:       # noqa: BLE001
            log.warning("sheet sync failed: %s", e)
        await asyncio.sleep(max(60, SHEETS_SYNC_MINUTES * 60))
