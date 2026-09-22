"""邮件账单：IMAP 轮询一个邮箱，把没读过的邮件正文交给 LLM 抽成消费记录。
Deliveroo / Uber Eats / Amazon 这些账单都是 HTML 邮件，转成纯文本再喂模型；
正文里认不出金额但带了图片附件（比如转发的小票照片）就走视觉模型。
一封邮件只处理一次（Message-ID），同一订单号只入一次账。"""
import email
import imaplib
import logging
import re
import time
from email.header import decode_header, make_header
from email.utils import parseaddr
from email.message import Message
from html.parser import HTMLParser

import database
import service
import telegram
import vision
from config import (MAIL_ALLOWED_SENDERS, MAIL_FOLDER, MAIL_IMAP_HOST, MAIL_IMAP_PORT, MAIL_PASSWORD,
                    MAIL_POLL_SECONDS, MAIL_USER)

log = logging.getLogger("ledger.mail")


def enabled() -> bool:
    return bool(MAIL_USER and MAIL_PASSWORD)


async def run():
    import asyncio
    if not enabled():
        log.info("MAIL_USER/MAIL_PASSWORD 未配置，邮件账单不启动")
        return
    while True:
        try:
            await asyncio.to_thread(poll_once)
        except Exception as e:  # noqa: BLE001
            log.warning("mail poll failed: %s", e)
            database.set_setting("mail_last_error", str(e)[:200])
        await asyncio.sleep(MAIL_POLL_SECONDS)


def poll_once() -> dict:
    """拉一次未读邮件。返回 {processed, skipped, errors}。"""
    result = {"processed": 0, "skipped": 0, "errors": 0}
    if not enabled():
        raise RuntimeError("邮箱未配置")
    with imaplib.IMAP4_SSL(MAIL_IMAP_HOST, MAIL_IMAP_PORT) as box:
        box.login(MAIL_USER, MAIL_PASSWORD)
        typ, _ = box.select(_quote_folder(MAIL_FOLDER))
        if typ != "OK":
            raise RuntimeError(f"打不开邮箱文件夹 {MAIL_FOLDER}")
        typ, data = box.uid("search", None, "UNSEEN")
        uids = data[0].split() if typ == "OK" and data and data[0] else []
        for uid in uids:
            typ, msgdata = box.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not msgdata or not isinstance(msgdata[0], tuple):
                continue
            msg = email.message_from_bytes(msgdata[0][1])
            status = _handle(msg)
            result[{"ok": "processed", "skipped": "skipped", "rejected": "skipped"}.get(status, "errors")] += 1
            box.uid("store", uid, "+FLAGS", "(\\Seen)")
    database.set_setting("mail_last_poll", str(int(time.time())))
    database.set_setting("mail_last_error", "")
    return result


def handle_raw(raw: bytes, envelope_from: str = "") -> str:
    """推送入口（Cloudflare Worker）：原始 MIME → 同一套处理。"""
    return _handle(email.message_from_bytes(raw), envelope_from)


GMAIL_VERIFY = "forwarding-noreply@google.com"


def sender_allowed(*addresses: str) -> bool:
    if not MAIL_ALLOWED_SENDERS:
        return True
    for raw in addresses:
        addr = parseaddr(raw or "")[1].lower().strip()
        if not addr:
            continue
        if addr == GMAIL_VERIFY:
            return True
        domain = "@" + addr.split("@")[-1]
        if addr in MAIL_ALLOWED_SENDERS or domain in MAIL_ALLOWED_SENDERS:
            return True
    return False


def _handle(msg: Message, envelope_from: str = "") -> str:
    message_id = (msg.get("Message-ID") or "").strip()
    subject = _decode(msg.get("Subject"))
    sender = _decode(msg.get("From"))
    if database.inbound_seen(message_id):
        return "skipped"
    # 白名单：自动转发时 From 头可能还是 Deliveroo，信封发件人才是你的邮箱，两个都看
    if not sender_allowed(sender, envelope_from):
        who = parseaddr(sender)[1] or envelope_from
        database.insert_inbound(message_id, subject, sender, "", "rejected", f"发件人不在白名单：{who}")
        log.info("email rejected from %s / %s", sender, envelope_from)
        return "rejected"
    text, images = extract(msg)
    # Gmail 设自动转发时会先发一封验证邮件，把链接直接推到 Telegram，点一下就行
    if parseaddr(sender)[1].lower() == GMAIL_VERIFY:
        link = re.search(r"https://mail-settings\.google\.com/\S+", text)
        detail = f"Gmail 转发验证：{link.group(0) if link else '（没找到链接，看邮件原文）'}"
        database.insert_inbound(message_id, subject, sender, "", "skipped", detail)
        telegram.notify(f"📧 Gmail 要确认转发到 ledger@，点这个链接：\n{link.group(0) if link else text[:500]}")
        return "skipped"
    header = f"Subject: {subject}\nFrom: {sender}\nDate: {msg.get('Date', '')}\n\n"
    parsed = None
    error = ""
    if len(text.strip()) > 40:
        try:
            parsed = vision.parse_email(header + text)
        except vision.VisionError as e:
            error = str(e)
    if parsed is None and images:
        try:
            parsed = vision.parse_receipt(*images[0], today=service.today().isoformat())
            if parsed.get("kind") != "receipt":
                parsed, error = None, "附件是流水截图，不是单笔账单"
            else:
                parsed["order_ref"] = ""
        except vision.VisionError as e:
            error = error or str(e)
    if parsed is None:
        database.insert_inbound(message_id, subject, sender, "", "error", error or "邮件里没有可识别的内容")
        log.info("email skipped: %s (%s)", subject, error)
        return "error"
    ref = parsed.get("order_ref", "")
    if database.inbound_ref_seen(ref):
        existing = database.find_by_ref(ref)
        if existing and not any(i.get("amount") for i in existing["items"]) and parsed["items"]:
            # 已有那笔没明细价格（比如短信补的）→ 这封邮件正好补上
            record = service.build_expense(parsed, source="email")
            merged = service.merge_into(existing, record)
            database.insert_inbound(message_id, subject, sender, ref, "ok", "补充明细到已有记录", merged["id"])
            telegram.notify("📧 这封邮件补上了明细 ✅\n" + service.expense_text(merged))
            return "ok"
        database.insert_inbound(message_id, subject, sender, ref, "skipped", "同一订单已入账")
        if existing:
            telegram.notify(f"📧 这封「{service._esc(subject[:40])}」是 {existing['date'][5:]} {service._esc(existing['merchant'])} "
                            f"£{existing['amount_gbp']:.2f} 那笔（已有 {len(existing['items'])} 项明细），没有重复记。"
                            f"\n要补的是别的订单？看看邮件里的订单号是不是这个：#{ref}")
        return "skipped"
    if ref and not parsed["note"]:
        parsed["note"] = f"#{ref}"
    image = service.save_image(*images[0][::-1]) if images and not text.strip() else ""
    record = service.build_expense(parsed, source="email", image=image)
    dup = service.find_duplicate(record)
    if dup:
        merged = service.merge_into(dup, record)
        database.insert_inbound(message_id, subject, sender, ref, "ok", "并入已有记录", merged["id"])
        telegram.notify("📧 邮件账单和已有记录是同一笔，已合并 ✅\n" + service.expense_text(merged))
        return "ok"
    record["id"] = database.insert_expense(record)
    database.insert_inbound(message_id, subject, sender, ref, "ok", "", record["id"])
    text_out = "📧 邮件账单记好了\n" + service.expense_text(record)
    if service.items_unpriced(record):
        text_out += "\n\n📎 邮件里没有单项价格。想要明细的话把订单详情页截图发给我，我会补到这笔上，不会重复记。"
    telegram.notify(text_out)
    return "ok"


# ---- MIME → text ---------------------------------------------------------

class _HtmlText(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "section", "hr"}
    CELL = {"td", "th"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in self.CELL:
            self.parts.append("  ")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head") and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\xa0]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n", raw)
        return "\n".join(line.strip() for line in raw.splitlines() if line.strip())


def html_to_text(html: str) -> str:
    p = _HtmlText()
    p.feed(html)
    return p.text()


def extract(msg: Message) -> tuple[str, list[tuple[bytes, str]]]:
    """返回 (正文文本, [(图片bytes, mime), ...])。HTML 正文比纯文本版更完整（外卖
    的 text/plain 常常只有一句 '请用支持 HTML 的客户端'），所以优先 HTML。"""
    plain, html = "", ""
    images: list[tuple[bytes, str]] = []
    for part in msg.walk():
        ctype = part.get_content_type()
        disp = str(part.get("Content-Disposition") or "")
        if ctype == "text/plain" and "attachment" not in disp:
            plain += _payload_text(part)
        elif ctype == "text/html" and "attachment" not in disp:
            html += _payload_text(part)
        elif ctype.startswith("image/"):
            data = part.get_payload(decode=True) or b""
            if len(data) > 40_000:            # 小于 40KB 的基本是 logo / 追踪像素
                images.append((data, ctype))
    text = html_to_text(html) if html else plain
    if html and len(text) < 80 and len(plain) > len(text):
        text = plain
    return text, images


def _payload_text(part: Message) -> str:
    data = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return str(value)


def _quote_folder(name: str) -> str:
    return f'"{name}"' if " " in name or "/" in name else name
