"""Telegram bot：长轮询（不用公网 webhook，后端不暴露）。
- 发小票/截图 → 识别入账 → 回一条明细
- 发「12.5 coffee」/「Tesco 23.4」 → 一句话记账
- /month /today /undo /help
- 首次：把网站密码发给 bot 就完成绑定（chat_id 存 settings）。"""
import asyncio
import json
import logging
import re

import httpx

import database
import pantry
import recall
import reminders
import service
import sheets
import vision
from config import ADMIN_TOKEN, SITE_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger("ledger.telegram")
API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HELP = """我是你的记账小助手 📒

• 直接发小票 / 支付截图 → 自动识别入账
• 发一句话，如 <code>12.5 coffee</code>、<code>Tesco 23.40</code>、<code>¥68 火锅</code>
• /today 今天记了什么
• /month 本月汇总
• 把银行 / 信用卡消费短信粘过来（一条或一堆）→ 自动对账，没记的补上
• 银行扣了钱但没小票的，想起来是什么就说「11.21 那笔是摆渡车的钱」；/recall 看还有哪些没说明
• 和室友之间转了钱就说「我转了 {R1} 99.47」或「{R2} 转给我 17.01」→ 记进合租表
• 说「提醒我明天吃饭的时候带小礼物」→ 到点提醒你；说「改到 18:00」改时间
• /reminders 看待提醒的事，<code>/rm 2</code> 取消第 2 条
• /undo 撤销上一笔（我记的或邮件来的）
• /help 这条说明"""

_AMOUNT_RE = re.compile(r"(?P<cur>£|¥|￥|€|\$|gbp|cny|eur|usd)?\s*(?P<num>\d+(?:[.,]\d{1,2})?)\s*(?P<cur2>£|¥|￥|€|\$|gbp|cny|eur|usd|镑|元)?", re.I)
_SMS_HINT = re.compile(r"spent|purchase|transaction|payment (?:of|to)|card (?:ending|\*)|debit|消费|支出|交易|人民币|信用卡|借记卡", re.I)
_CUR = {"£": "GBP", "gbp": "GBP", "镑": "GBP", "¥": "CNY", "￥": "CNY", "cny": "CNY", "元": "CNY",
        "€": "EUR", "eur": "EUR", "$": "USD", "usd": "USD"}


def _help() -> str:
    """帮助里的转账示例用花名册里真实的室友名字（名字不进代码）。"""
    mates = (sheets.roster()["roommates"] + ["Alex", "Sam"])[:2]
    return HELP.replace("{R1}", mates[0]).replace("{R2}", mates[1])


def enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN)


def bound_chat_id() -> str:
    return database.get_setting("telegram_chat_id") or TELEGRAM_CHAT_ID


def send_message(chat_id: str | int, text: str, disable_preview: bool = True) -> bool:
    if not enabled() or not chat_id:
        return False
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(f"{API}/sendMessage", json={
                "chat_id": chat_id, "text": text, "parse_mode": "HTML",
                "disable_web_page_preview": disable_preview,
            })
        if resp.status_code != 200:
            log.warning("sendMessage %s: %s", resp.status_code, resp.text[:200])
        return resp.status_code == 200
    except Exception as e:  # noqa: BLE001
        log.warning("sendMessage failed: %s", e)
        return False


def notify(text: str) -> bool:
    """发给已绑定的那个人。"""
    return send_message(bound_chat_id(), text)


async def run_polling():
    if not enabled():
        log.info("TELEGRAM_BOT_TOKEN 未配置，bot 不启动")
        return
    await asyncio.to_thread(_set_commands)
    offset = int(database.get_setting("telegram_offset") or 0)
    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            try:
                resp = await client.get(f"{API}/getUpdates", params={
                    "offset": offset, "timeout": 30, "allowed_updates": '["message"]'})
                if resp.status_code != 200:
                    log.warning("getUpdates %s: %s", resp.status_code, resp.text[:200])
                    await asyncio.sleep(5)
                    continue
                for upd in resp.json().get("result", []):
                    offset = upd["update_id"] + 1
                    database.set_setting("telegram_offset", str(offset))
                    try:
                        await _handle(client, upd.get("message") or {})
                    except Exception as e:  # noqa: BLE001
                        log.exception("handle update failed: %s", e)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("polling error: %s", e)
                await asyncio.sleep(5)


def _set_commands():
    try:
        with httpx.Client(timeout=10) as client:
            client.post(f"{API}/setMyCommands", json={"commands": [
                {"command": "today", "description": "今天记了什么"},
                {"command": "month", "description": "本月汇总"},
                {"command": "split", "description": "待收的分账 / 代付"},
                {"command": "recall", "description": "银行扣了钱、还不知道是什么的"},
                {"command": "fridge", "description": "食材库存 / 快过期"},
                {"command": "reminders", "description": "待提醒的事"},
                {"command": "ok", "description": "确认疑似同一笔（对账后）"},
                {"command": "undo", "description": "撤销上一笔"},
                {"command": "help", "description": "怎么用"},
            ]})
    except Exception:  # noqa: BLE001
        pass


async def _handle(client: httpx.AsyncClient, msg: dict):
    chat_id = str(msg.get("chat", {}).get("id") or "")
    if not chat_id:
        return
    text = (msg.get("text") or msg.get("caption") or "").strip()

    # 绑定：把网站密码发过来
    if ADMIN_TOKEN and text == ADMIN_TOKEN:
        database.set_setting("telegram_chat_id", chat_id)
        send_message(chat_id, "绑定好了 ✅ 以后提醒会发到这里。\n\n" + _help())
        return
    if chat_id != bound_chat_id():
        send_message(chat_id, "这是私人账本。把网站密码发给我完成绑定。")
        return

    if text.lower().startswith("/ok"):
        await asyncio.to_thread(_confirm_adjust, chat_id, text)
        return
    if text.lower().startswith("/no"):
        database.set_setting("pending_adjust", "")
        send_message(chat_id, "好，忽略这些疑似记录。")
        return
    if text.lower().startswith("/used") or text.lower().startswith("/toss"):
        await asyncio.to_thread(_pantry_mark, chat_id, text)
        return
    if text.lower().startswith("/rm"):
        await asyncio.to_thread(_cancel_reminder, chat_id, text)
        return
    if text.lower().startswith("/sms"):
        await asyncio.to_thread(_reconcile, chat_id, text[4:].strip())
        return
    if text.startswith("/"):
        await asyncio.to_thread(_command, chat_id, text.split()[0].lower().split("@")[0])
        return

    photo = msg.get("photo")
    doc = msg.get("document")
    if photo or (doc and str(doc.get("mime_type", "")).startswith("image/")):
        file_id = photo[-1]["file_id"] if photo else doc["file_id"]
        mime = doc["mime_type"] if doc else "image/jpeg"
        send_message(chat_id, "收到，识别中… 🔍")
        data = await _download(client, file_id)
        await asyncio.to_thread(_ingest_image, chat_id, data, mime, text)
        return

    # 提醒要在记账之前判断：「提醒我明天交 50 镑房租」里也有金额
    if text and reminders.is_request(text):
        await asyncio.to_thread(_add_reminder, chat_id, text)
        return
    if text and reminders.RESCHEDULE.match(text) and reminders.last_created_pending():
        await asyncio.to_thread(_reschedule, chat_id, text)
        return

    # 转账、补充说明都带金额 / 日期数字，要赶在「一句话记账」前面认出来，否则会被记成一笔新消费
    if text and recall.is_note(text):
        await asyncio.to_thread(_recall_note, chat_id, text)
        return
    if text and sheets.enabled() and _transfer_parts(text):
        await asyncio.to_thread(_record_transfer, chat_id, text)
        return
    if text and _looks_like_sms(text):
        await asyncio.to_thread(_reconcile, chat_id, text)
        return
    if text:
        await asyncio.to_thread(_quick_add, chat_id, text)


async def _download(client: httpx.AsyncClient, file_id: str) -> bytes:
    info = (await client.get(f"{API}/getFile", params={"file_id": file_id})).json()
    path = info["result"]["file_path"]
    resp = await client.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{path}")
    return resp.content


def _ingest_image(chat_id: str, data: bytes, mime: str, caption: str):
    try:
        parsed = vision.parse_receipt(data, mime, service.today().isoformat())
    except vision.VisionError as e:
        send_message(chat_id, f"识别失败：{service._esc(str(e))}\n可以直接发一句「金额 用途」手动记。")
        return
    if parsed.get("kind") == "transactions":
        # 银行流水 / 短信列表的截图 → 对账，不当小票记
        _reconcile_txs(chat_id, parsed["transactions"], intro="这是一张流水截图，按对账处理：")
        return
    if caption and not parsed["note"]:
        parsed["note"] = caption[:200]
    image = service.save_image(data, mime)
    record = service.build_expense(parsed, source="telegram", image=image)
    dup = service.find_duplicate(record, date_known=bool(parsed.get("date")))
    if dup:
        merged = service.merge_into(dup, record)
        send_message(chat_id, "这笔已经有了，把明细补上去了 ✅\n" + service.expense_text(merged))
        return
    record["id"] = database.insert_expense(record)
    send_message(chat_id, "记好了 ✅\n" + service.expense_text(record))


def _quick_add(chat_id: str, text: str):
    m = _AMOUNT_RE.search(text)
    if not m:
        send_message(chat_id, "没找到金额。像这样：<code>12.5 coffee</code> 或 <code>Tesco 23.40</code>\n/help 看用法")
        return
    amount = float(m.group("num").replace(",", "."))
    currency = _CUR.get((m.group("cur") or m.group("cur2") or "").lower(), "GBP")
    description = (text[:m.start()] + " " + text[m.end():]).strip(" ,，-—:：")
    guess = vision.classify_text(description) if description else {"merchant": "", "category": "other", "note": ""}
    record = service.build_expense({
        "amount": amount, "currency": currency, "merchant": guess["merchant"],
        "category": guess["category"], "note": guess["note"],
        "items": [{"name": guess["merchant"] or "消费", "qty": 1, "amount": amount, "category": guess["category"]}],
    }, source="telegram")
    record["id"] = database.insert_expense(record)
    send_message(chat_id, "记好了 ✅\n" + service.expense_text(record))


# ---- 和室友之间的转账 → 合租表 ------------------------------------------------

_TRANSFER_RE = re.compile(r"转账|转了|转给|转我|给我转|还给|还我|还了|收到|转|transfer|paid (?:me|back)|sent", re.I)
_TO_ME = re.compile(r"(?:给|转|还)\s*我|收到|paid me|sent me|to me", re.I)
_ME_FIRST = re.compile(r"我\s*(?:已经|刚)?\s*(?:转|还|给)|\bi (?:sent|paid|transferred)", re.I)


def _transfer_parts(text: str) -> tuple[str, str, float, str] | None:
    """「我转了 Alex 99.47」→ (转钱的, 收钱的, 金额, 日期)。认不出来返回 None。"""
    kw = _TRANSFER_RE.search(text)
    if not kw:
        return None
    me = sheets.SHEETS_ME
    names = sorted(((m.start(), p) for p in sheets.SHEETS_PEOPLE if p != me
                    for m in re.finditer(re.escape(p), text, re.I)), key=lambda x: x[0])
    others = list(dict.fromkeys(p for _, p in names))
    nums = [m for m in _AMOUNT_RE.finditer(text) if m.group("num")]
    if not others or not nums:
        return None
    money = next((m for m in nums if m.group("cur") or m.group("cur2")), nums[-1])
    amount = float(money.group("num").replace(",", "."))
    if len(others) >= 2:
        sender, receiver = others[0], others[1]
    elif _TO_ME.search(text):
        sender, receiver = others[0], me
    elif _ME_FIRST.search(text):
        sender, receiver = me, others[0]
    elif names[0][0] < kw.start():                  # 「Sam 转账 17」：名字在动词前面 → 对方转给我
        sender, receiver = others[0], me
    else:                                           # 「转 Alex 99.47」
        sender, receiver = me, others[0]
    back = 2 if "前天" in text else 1 if "昨天" in text else 0
    return sender, receiver, amount, (service.today() - service.timedelta(days=back)).isoformat()


def _record_transfer(chat_id: str, text: str):
    sender, receiver, amount, day = _transfer_parts(text)
    try:
        t = sheets.add_transfer(sender, receiver, amount, day)
    except Exception as e:  # noqa: BLE001
        send_message(chat_id, f"没记进合租表：{service._esc(str(e))}")
        return
    database.set_setting("last_transfer", json.dumps({"ref": t["ref"], "at": int(service.now_local().timestamp())}))
    me = sheets.SHEETS_ME
    other = receiver if sender == me else sender
    try:
        left = sheets.pair_balance(me, other)      # 我还欠 other 多少
        status = ("两清 ✓" if abs(left) < 0.05 else f"你还要转给 {other} £{left:.2f}" if left > 0
                  else f"{other} 还要转给你 £{-left:.2f}")
        status = f"\n现在你和 {other}：{status}"
    except Exception:  # noqa: BLE001
        status = ""
    send_message(chat_id, f"💸 记进合租表了：{day[5:]} {sender} → {receiver} £{amount:.2f}{status}\n"
                          f"方向或金额不对就回 /undo 撤销。")


# ---- 银行扣了钱、没小票的：补充说明 ----------------------------------------------

def _recall_note(chat_id: str, text: str):
    found, desc = recall.pick(text)
    if not found:
        send_message(chat_id, "没找到是哪一笔。说日期或金额都行，比如「11.21 那笔是摆渡车」「£3.50 那笔是…」；/recall 看还没说明的。")
        return
    if len(found) > 1:
        recall.remember_list(found[:9])
        lines = [f"对上了 {len(found)} 笔，是哪一笔？", ""] + [recall.line(k, e) for k, e in enumerate(found[:9], 1)]
        lines.append(f"\n回我「第 1 笔是{service._esc(desc)}」")
        send_message(chat_id, "\n".join(lines))
        return
    e = recall.apply(found[0], desc, vision.classify_text)
    # 不能提示 /undo：那会把整笔银行扣款删掉
    text = service.expense_text(e).replace(" · /undo 撤销", "")
    send_message(chat_id, f"补上了 ✅\n{text}\n说错了就再说一遍「{e['date'][5:].replace('-', '.')} 那笔是…」，会覆盖。")


def _add_reminder(chat_id: str, text: str):
    r, guessed = reminders.create(text)
    tail = "\n没说具体几点，先定在这个时间；想换就回我「改到 18:00」。" if guessed else "\n时间不对就回我「改到 18:00」。"
    send_message(chat_id, f"⏰ 好的，{reminders.when_text(r['due_at'])} 提醒你：\n{service._esc(r['text'])}{tail}")


def _reschedule(chat_id: str, text: str):
    r = reminders.last_created_pending()
    due = reminders.parse_time_only(reminders.RESCHEDULE.match(text).group(1), r["due_at"])
    if not due:
        send_message(chat_id, "没看懂新时间。像这样：<code>改到 18:00</code> 或 <code>改到明天下午3点</code>")
        return
    database.update_reminder(r["id"], due_at=due)
    send_message(chat_id, f"改好了 ⏰ {reminders.when_text(due)} 提醒你：{service._esc(r['text'])}")


def _pending_reminders() -> list[dict]:
    return database.list_reminders(status="pending")


def _cancel_reminder(chat_id: str, text: str):
    rows = _pending_reminders()
    picks = [int(x) for x in re.findall(r"\d+", text[3:])]
    done = [rows[k - 1] for k in picks if 1 <= k <= len(rows)]
    for r in done:
        database.update_reminder(r["id"], status="cancelled")
    send_message(chat_id, ("已取消：" + "、".join(service._esc(r["text"]) for r in done)) if done else "编号不对，/reminders 再看一眼")


def _looks_like_sms(text: str) -> bool:
    amounts = len(re.findall(r"(?:£|¥|￥|€|\$)\s?\d", text))
    return bool(_SMS_HINT.search(text)) or amounts >= 2 or ("\n" in text.strip() and amounts >= 1)


def _reconcile(chat_id: str, text: str):
    if not text:
        send_message(chat_id, "把银行短信粘在 /sms 后面，或者直接整段发过来。")
        return
    today = service.today().isoformat()
    txs = vision.parse_sms(text, today)
    if not txs:
        send_message(chat_id, "没从这段文字里找到消费记录。")
        return
    _reconcile_txs(chat_id, txs)


def _reconcile_txs(chat_id: str, txs: list[dict], intro: str = "对账结果："):
    results = service.reconcile(txs)
    lines = []
    added = 0
    new_ids: list[tuple[int, str]] = []
    pending: list[dict] = []
    for r in results:
        tx, e = r["tx"], r["expense"]
        head = f"{tx['date'][5:]} {service._esc(tx['merchant'] or '—')} {tx['currency']} {tx['amount']:.2f}"
        if r["status"] == "matched":
            lines.append(f"✅ {head} → 已记（{service._esc(e['merchant'])}）")
        elif r["status"] == "adjust":
            pending.append({"expense_id": e["id"], "tx": tx, "label": f"{e['merchant']} £{e['amount_gbp']:.2f} → £{tx['amount']:.2f}"})
            lines.append(f"❓ <b>{len(pending)}.</b> {head} → 疑似是 {e['date'][5:]} {service._esc(e['merchant'])} £{e['amount_gbp']:.2f} 那笔，"
                         f"差 £{r['diff_gbp']:.2f}（配送费 / 差价？）")
        elif r["status"] == "maybe":
            lines.append(f"❓ {head} → 可能是 {e['date'][5:]} {service._esc(e['merchant'])}，没另记；不是的话回我「{tx['amount']:.2f} {service._esc(tx['merchant'])}」手动记")
        elif r["status"] == "ignored":
            lines.append(f"➖ {head}（{tx['kind']}，跳过）")
        else:
            rec = service.record_sms(tx, vision.classify_text)
            added += 1
            new_ids.append((rec["id"], rec["date"][5:].replace("-", ".")))
            lines.append(f"🆕 {head} → 没记过，已按短信记为「{service._esc(rec['merchant'])}」")
    tail = (f"\n\n补记了 {added} 笔。有小票就发我（会自动并进去）；没小票的话，想起来是什么就回我"
            f"「{new_ids[0][1]} 那笔是…」。" if added else "")
    recall.mark_asked([i for i, _ in new_ids])
    if pending:
        database.set_setting("pending_adjust", json.dumps(pending, ensure_ascii=False))
        tail += ("\n\n带 ❓ 的是疑似同一笔：回复 <b>/ok</b> 把它们的金额改成短信金额（差额记成一行配送费/差价）；"
                 "只确认某几笔就 <code>/ok 1 3</code>；/no 忽略。")
    else:
        database.set_setting("pending_adjust", "")
    send_message(chat_id, intro + "\n" + "\n".join(lines) + tail)


def _confirm_adjust(chat_id: str, text: str):
    raw = database.get_setting("pending_adjust")
    pending = json.loads(raw) if raw else []
    if not pending:
        send_message(chat_id, "没有待确认的疑似记录。先发一段短信或流水截图给我对账。")
        return
    picks = [int(x) for x in re.findall(r"\d+", text)]
    chosen = [p for i, p in enumerate(pending, 1) if not picks or i in picks]
    done = []
    for p in chosen:
        upd = service.apply_adjust(p["expense_id"], p["tx"])
        if upd:
            done.append(f"✅ {service._esc(upd['merchant'])} 改成 £{upd['amount_gbp']:.2f}")
    database.set_setting("pending_adjust", "")
    send_message(chat_id, "\n".join(done) if done else "没有更新任何记录。")


def _pantry_mark(chat_id: str, text: str):
    status = "tossed" if text.lower().startswith("/toss") else "used"
    picks = [int(x) for x in re.findall(r"\d+", text)]
    if not picks:
        send_message(chat_id, "要标哪几项？先 /fridge 看编号，再 <code>/used 1 3</code>")
        return
    ov = pantry.overview(service.today())
    ordered = ov["soon"] + ov["fresh"] + ov["dry"]       # 和 /fridge 的编号一致
    done = []
    for k in picks:
        if 1 <= k <= len(ordered):
            database.set_pantry_status(ordered[k - 1]["id"], status)
            done.append(ordered[k - 1]["name"])
    send_message(chat_id, ("✅ 已标" + ("用完" if status == "used" else "扔了") + "：" + "、".join(service._esc(d) for d in done)) if done else "编号不对，/fridge 再看一眼")


def _command(chat_id: str, cmd: str):
    if cmd in ("/start", "/help"):
        send_message(chat_id, _help())
    elif cmd == "/today":
        day = service.today().isoformat()
        rows = database.list_expenses(day=day)
        if not rows:
            send_message(chat_id, "今天还没记账 ~ 发张小票或者一句「金额 用途」就行。")
            return
        total = sum(r["amount_gbp"] for r in rows)
        lines = [f"📅 今天 {day}，{len(rows)} 笔，共 £{total:,.2f}", ""]
        for r in rows:
            lines.append(f"• {service._esc(r['merchant'] or '—')}  £{r['amount_gbp']:.2f}")
        send_message(chat_id, "\n".join(lines))
    elif cmd == "/reminders":
        rows = _pending_reminders()
        if not rows:
            send_message(chat_id, "没有待提醒的事。跟我说「提醒我…」就能加。")
            return
        lines = ["⏰ 待提醒：", ""]
        lines += [f"{k}. {reminders.when_text(r['due_at'])}  {service._esc(r['text'])}" for k, r in enumerate(rows, 1)]
        lines.append("\n回复 <code>/rm 1</code> 取消")
        send_message(chat_id, "\n".join(lines))
    elif cmd == "/month":
        send_message(chat_id, service.month_report_text(service.today().strftime("%Y-%m")))
    elif cmd == "/fridge":
        ov = pantry.overview(service.today())
        if not ov["count"]:
            send_message(chat_id, "库存是空的。买菜的小票 / Ocado 邮件入账后，能坏的食材会自动进来。")
            return
        lines = [f"🧊 食材库存 {ov['count']} 项：", ""]
        k = 0
        for title, rows in (("⚠️ 快过期", ov["soon"]), ("🥬 新鲜", ov["fresh"]), ("🍚 干货", ov["dry"])):
            if not rows:
                continue
            lines.append(f"<b>{title}</b>")
            for r in rows:
                k += 1
                left = r["days_left"]
                when = f"已过期 {-left} 天" if left < 0 else "今天到期" if left == 0 else f"剩 {left} 天"
                label = f"{r['name']} · {r['name_raw']}" if r["name_raw"] and r["name_raw"] != r["name"] else r["name"]
                lines.append(f"{k}. {service._esc(label)}（{r['bought'][5:]} 买，{when}）")
            lines.append("")
        lines.append("回复 <code>/used 1 3</code> 标用完，<code>/toss 2</code> 标扔了")
        send_message(chat_id, "\n".join(lines))
    elif cmd == "/split":
        rows = [r for r in database.list_split() if not r["settled"]]
        if not rows:
            send_message(chat_id, "没有待收的款 🎉")
            return
        lines = [f"👥 待收 £{sum(r['owed_gbp'] for r in rows):,.2f}，{len(rows)} 笔：", ""]
        for r in rows[:20]:
            who = f" · {service._esc(r['split_with'])}" if r["split_with"] else ""
            kind = f"{r['split_n']}人分" if r["split_kind"] == "split" else "代付"
            lines.append(f"• {r['date'][5:]} {service._esc(r['merchant'] or '—')} £{r['amount_gbp']:.2f} {kind}{who} → 待收 £{r['owed_gbp']:.2f}")
        lines.append(f'\n<a href="{SITE_URL}/split">去网页收款 / 导出表格</a>')
        send_message(chat_id, "\n".join(lines))
    elif cmd == "/recall":
        rows = recall.unexplained()
        if not rows:
            send_message(chat_id, "银行扣款都有说明了 🎉")
            return
        recall.remember_list(rows[:20])
        recall.mark_asked([e["id"] for e in rows])
        send_message(chat_id, recall.list_text(rows[:20], f"🤔 这 {len(rows)} 笔银行扣了钱，还不知道是什么："))
    elif cmd == "/undo":
        last = database.last_expense(sources=("telegram", "email"))
        tr = json.loads(database.get_setting("last_transfer") or "{}")
        if tr and (not last or tr["at"] >= last["created_at"]):
            database.set_setting("last_transfer", "")
            ok = sheets.delete_transfer(tr["ref"])
            send_message(chat_id, "已从合租表撤销刚才那笔转账。" if ok else "合租表里没找到刚才那笔转账（可能已经被删了）。")
            return
        if not last:
            send_message(chat_id, "没有可撤销的记录。")
            return
        database.delete_expense(last["id"])
        service.delete_image(last["image"])
        send_message(chat_id, f"已撤销：{service._esc(last['merchant'] or '—')} £{last['amount_gbp']:.2f}")
    else:
        send_message(chat_id, "不认识这个命令。\n" + _help())
