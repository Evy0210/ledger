"""ledger backend — 私人账本，所有接口需要 Bearer <ADMIN_TOKEN>
（网站密码），只有 /api/login 和 /api/health 例外。"""
import asyncio
import logging
import re
import threading
from contextlib import asynccontextmanager
from urllib.parse import unquote

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import database
import mail
import pantry
import scheduler
import service
import sheets
import telegram
import vision
from categories import CATEGORIES, SUBCATEGORIES
from config import (ADMIN_TOKEN, DEMO_MODE, MAIL_INBOUND_ADDRESS, MAIL_INBOUND_SECRET, MAIL_USER, RECEIPTS_DIR,
                    RECIPES_URL, VISION_MODEL)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
database.init_db()
if DEMO_MODE:
    import demo_seed
    demo_seed.rebuild()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if DEMO_MODE:          # 演示实例不发 Telegram、不收邮件、不同步表
        yield
        return
    tasks = [asyncio.create_task(telegram.run_polling()), asyncio.create_task(scheduler.run()),
             asyncio.create_task(mail.run()), asyncio.create_task(sheets.run())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="ledger", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3070", "http://127.0.0.1:3070"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_demo_lock = asyncio.Lock()


@app.middleware("http")
async def demo_guard(request: Request, call_next):
    """演示实例：只读；跨天了就重建假数据，日期永远是「最近」。"""
    if not DEMO_MODE:
        return await call_next(request)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path != "/api/login":
        return JSONResponse({"detail": "演示模式：数据是虚构的，不能修改。输入密码进入真实账本。"}, status_code=403)
    if database.get_setting("demo_seeded") != service.today().isoformat():
        async with _demo_lock:
            if database.get_setting("demo_seeded") != service.today().isoformat():
                await asyncio.to_thread(demo_seed.rebuild)
    return await call_next(request)


def require_auth(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    try:
        token = unquote(token.encode("latin-1").decode("utf-8"))
    except (UnicodeDecodeError, UnicodeEncodeError):
        token = unquote(token)
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(401, "请先登录")


@app.get("/api/health")
def health():
    return {"ok": True}


class LoginRequest(BaseModel):
    password: str


@app.post("/api/login")
def login(req: LoginRequest):
    if not ADMIN_TOKEN or req.password.strip() != ADMIN_TOKEN:
        raise HTTPException(401, "密码不对")
    return {"token": ADMIN_TOKEN}


@app.get("/api/status", dependencies=[Depends(require_auth)])
def status():
    st = service.status()
    st["vision_ready"] = vision.ready()
    st["vision_model"] = VISION_MODEL
    st["telegram_enabled"] = telegram.enabled()
    st["categories"] = CATEGORIES
    st["subcategories"] = SUBCATEGORIES
    st.update(_mail_status())
    return st


# ---- expenses ----------------------------------------------------------

class ItemIn(BaseModel):
    name: str = ""
    qty: float = 1
    amount: float = 0
    category: str = ""
    sub: str = ""
    who: str = ""            # 分账时：me 我的 | them 别人的 | shared/'' 公用
    name_zh: str = ""
    shelf_days: int = 0


class ExpenseIn(BaseModel):
    date: str = ""
    merchant: str = ""
    category: str = "other"
    currency: str = "GBP"
    amount: float
    items: list[ItemIn] = []
    note: str = ""
    image: str = ""          # 识别后暂存的小票文件名（/api/receipts/parse 返回）
    order_ref: str = ""


@app.get("/api/expenses", dependencies=[Depends(require_auth)])
def list_expenses(month: str = "", day: str = "", limit: int = 0, start: str = "", end: str = ""):
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400, "month 格式 YYYY-MM")
    _check_range(start, end)
    return {"expenses": database.list_expenses(month=month or None, day=day or None, limit=limit,
                                               start=start or None, end=end or None)}


def _check_range(start: str, end: str):
    for v in (start, end):
        if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise HTTPException(400, "日期格式 YYYY-MM-DD")
    if bool(start) != bool(end):
        raise HTTPException(400, "start 和 end 要一起给")


@app.post("/api/expenses", dependencies=[Depends(require_auth)])
def create_expense(req: ExpenseIn):
    try:
        record = service.build_expense(req.model_dump(), source="receipt" if req.image else "manual",
                                       image=_safe_name(req.image))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    record["id"] = database.insert_expense(record)
    return record


@app.get("/api/expenses/{expense_id}", dependencies=[Depends(require_auth)])
def get_expense(expense_id: int):
    exp = database.get_expense(expense_id)
    if not exp:
        raise HTTPException(404, "没有这笔记录")
    return exp


@app.put("/api/expenses/{expense_id}", dependencies=[Depends(require_auth)])
def update_expense(expense_id: int, req: ExpenseIn):
    exp = database.get_expense(expense_id)
    if not exp:
        raise HTTPException(404, "没有这笔记录")
    try:
        record = service.build_expense(req.model_dump(), source=exp["source"], image=req.image or exp["image"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if req.image and exp["image"] and req.image != exp["image"]:
        service.delete_image(exp["image"])
    database.update_expense(expense_id, record)
    return database.get_expense(expense_id)


@app.delete("/api/expenses/{expense_id}", dependencies=[Depends(require_auth)])
def delete_expense(expense_id: int):
    exp = database.delete_expense(expense_id)
    if not exp:
        raise HTTPException(404, "没有这笔记录")
    service.delete_image(exp["image"])
    return {"ok": True}


# ---- split / 代付 ---------------------------------------------------------

class SplitIn(BaseModel):
    split_kind: str = ""          # '' | split | paid_for
    split_n: int = 1
    split_with: str = ""
    settled: bool = False
    item_who: list[str] | None = None     # 和 items 顺序对齐


@app.patch("/api/expenses/{expense_id}/split", dependencies=[Depends(require_auth)])
def set_split(expense_id: int, req: SplitIn):
    if not database.get_expense(expense_id):
        raise HTTPException(404, "没有这笔记录")
    kind = req.split_kind if req.split_kind in ("", "split", "paid_for") else ""
    n = req.split_n if kind == "split" else 1
    if kind == "split" and n < 2:
        raise HTTPException(400, "分账至少 2 个人")
    database.set_split(expense_id, kind, n, req.split_with.strip(), req.settled if kind else False, req.item_who)
    _sync_sheet_soon()
    return database.get_expense(expense_id)


def _sync_sheet_soon():
    """分账一改就往合租表推一次。端点是同步的（跑在线程池里），所以直接另起一个线程，
    不碰事件循环；失败只记日志，不影响本站。"""
    if not sheets.enabled():
        return

    def _go():
        try:
            sheets.sync()
        except Exception as e:  # noqa: BLE001
            logging.getLogger("ledger.app").warning("sheet sync failed: %s", e)

    threading.Thread(target=_go, daemon=True).start()


@app.post("/api/sheet/sync", dependencies=[Depends(require_auth)])
async def sheet_sync():
    """手动同步一次，返回这轮推了/拉了多少。"""
    if not sheets.enabled():
        raise HTTPException(503, "服务器没配 SHEETS_ID / SHEETS_SA_JSON")
    try:
        result = await asyncio.to_thread(sheets.sync)
        return {**result, **await asyncio.to_thread(sheets.overview)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"同步失败：{e}") from e


@app.get("/api/sheet/people", dependencies=[Depends(require_auth)])
def sheet_people():
    """花名册。前端拿它把「和谁分」做成标签选择，不读表。"""
    return sheets.roster()


@app.get("/api/sheet", dependencies=[Depends(require_auth)])
async def sheet_overview():
    """合租表现状：每行账单 + 每人净额 + 建议转账。不用开表就能看。"""
    if not sheets.enabled():
        return {"enabled": False, "people": [], "rows": []}
    try:
        return await asyncio.to_thread(sheets.overview)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"读不到合租表：{e}") from e


@app.get("/api/split", dependencies=[Depends(require_auth)])
def list_split(month: str = ""):
    rows = database.list_split(month or None)
    return {"expenses": rows,
            "owed_gbp": round(sum(r["owed_gbp"] for r in rows), 2),
            "unsettled": sum(1 for r in rows if not r["settled"])}


# ---- receipts ----------------------------------------------------------

@app.post("/api/receipts/parse", dependencies=[Depends(require_auth)])
async def parse_receipt(file: UploadFile):
    """识别但不入库：前端拿到草稿让人改一改再 POST /api/expenses。
    图片先落盘，返回文件名，保存时带上。"""
    if not vision.ready():
        raise HTTPException(503, "服务器没配 VISION_API_KEY，识别功能不可用")
    data = await file.read()
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(413, "图片太大了（>12MB）")
    mime = file.content_type or "image/jpeg"
    try:
        parsed = await asyncio.to_thread(vision.parse_receipt, data, mime, service.today().isoformat())
    except vision.VisionError as e:
        raise HTTPException(422, str(e)) from e
    if parsed.get("kind") == "transactions":
        # 银行流水截图：不存图、不记账，交给前端的对账页
        return {"kind": "transactions", "results": service.reconcile(parsed["transactions"])}
    parsed["image"] = service.save_image(data, mime)
    # 已经有同一笔（邮件先到了）？前端提示「补到那笔」而不是再记一笔
    dup = service.find_duplicate(service.build_expense(parsed, source="receipt"), date_known=bool(parsed.get("date")))
    parsed["duplicate"] = {"id": dup["id"], "merchant": dup["merchant"], "date": dup["date"],
                           "amount_gbp": dup["amount_gbp"], "has_items": any(i.get("amount") for i in dup["items"])} if dup else None
    return parsed


@app.get("/api/receipts/{name}", dependencies=[Depends(require_auth)])
def get_receipt(name: str):
    path = RECEIPTS_DIR / _safe_name(name)
    if not name or not path.exists():
        raise HTTPException(404, "没有这张图")
    return FileResponse(path)


def _safe_name(name: str) -> str:
    name = (name or "").strip()
    if name and not re.fullmatch(r"[\w.-]+", name):
        raise HTTPException(400, "文件名不合法")
    return name


# ---- summaries ---------------------------------------------------------

@app.get("/api/summary", dependencies=[Depends(require_auth)])
def summary(month: str = ""):
    month = month or service.today().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400, "month 格式 YYYY-MM")
    s = database.month_summary(month)
    s["days_in_month"] = service.days_in_month(month)
    s["prev_month_total_gbp"] = round(database.month_total(service._prev_month(month)), 2)
    budget = database.get_setting("budget_gbp")
    s["budget_gbp"] = float(budget) if budget else None
    return s


@app.get("/api/days", dependencies=[Depends(require_auth)])
def days(start: str, end: str):
    _check_range(start, end)
    return {"days": database.daily_totals(start, end)}


@app.get("/api/months", dependencies=[Depends(require_auth)])
def months():
    return {"months": database.list_months()}


# ---- settings ----------------------------------------------------------

EDITABLE_SETTINGS = ("reminder_enabled", "reminder_hour", "reminder_mode", "monthly_report", "budget_gbp", "pantry_reminder", "pantry_hour")


class SettingsIn(BaseModel):
    reminder_enabled: bool | None = None
    reminder_hour: int | None = None
    reminder_mode: str | None = None
    monthly_report: bool | None = None
    budget_gbp: float | None = None
    pantry_reminder: bool | None = None
    pantry_hour: int | None = None


@app.get("/api/settings", dependencies=[Depends(require_auth)])
def get_settings():
    return _settings_view()


@app.put("/api/settings", dependencies=[Depends(require_auth)])
def put_settings(req: SettingsIn):
    if req.reminder_enabled is not None:
        database.set_setting("reminder_enabled", "1" if req.reminder_enabled else "0")
    if req.reminder_hour is not None:
        database.set_setting("reminder_hour", str(max(0, min(23, req.reminder_hour))))
    if req.reminder_mode in ("daily", "weekdays", "weekly"):
        database.set_setting("reminder_mode", req.reminder_mode)
    if req.monthly_report is not None:
        database.set_setting("monthly_report", "1" if req.monthly_report else "0")
    if req.budget_gbp is not None:
        database.set_setting("budget_gbp", str(req.budget_gbp) if req.budget_gbp > 0 else "")
    if req.pantry_reminder is not None:
        database.set_setting("pantry_reminder", "1" if req.pantry_reminder else "0")
    if req.pantry_hour is not None:
        database.set_setting("pantry_hour", str(max(0, min(23, req.pantry_hour))))
    return _settings_view()


@app.post("/api/settings/test-telegram", dependencies=[Depends(require_auth)])
def test_telegram():
    if not telegram.enabled():
        raise HTTPException(503, "服务器没配 TELEGRAM_BOT_TOKEN")
    if not telegram.bound_chat_id():
        raise HTTPException(400, "还没绑定：在 Telegram 里把网站密码发给 bot")
    if not telegram.notify("测试消息 👋 提醒会从这里发。"):
        raise HTTPException(502, "发送失败，看看后端日志")
    return {"ok": True}


@app.post("/api/settings/unbind-telegram", dependencies=[Depends(require_auth)])
def unbind_telegram():
    database.set_setting("telegram_chat_id", "")
    return _settings_view()


# ---- pantry / 食材库存 ------------------------------------------------------

@app.get("/api/pantry", dependencies=[Depends(require_auth)])
def get_pantry():
    return {**pantry.overview(service.today()), "recipes_url": RECIPES_URL}


class PantryStatusIn(BaseModel):
    status: str = "used"          # used | tossed | in
    expires: str = ""


@app.post("/api/pantry/{pantry_id}", dependencies=[Depends(require_auth)])
def set_pantry(pantry_id: int, req: PantryStatusIn):
    if req.expires:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", req.expires):
            raise HTTPException(400, "日期格式 YYYY-MM-DD")
        database.set_pantry_expires(pantry_id, req.expires)
    if req.status in ("used", "tossed", "in"):
        database.set_pantry_status(pantry_id, req.status)
    return {"ok": True}


@app.post("/api/items/enrich", dependencies=[Depends(require_auth)])
async def enrich_items(month: str = "", only_missing: bool = True, limit: int = 12):
    """给已有明细补中文名 / 子类 / 保质期（老记录识别时还没这些字段）。
    每次最多 limit 笔（每笔一次 LLM 调用约 3 秒，Next 反代 60 秒会超时），前端循环直到 remaining=0。"""
    if not vision.ready():
        raise HTTPException(503, "服务器没配 VISION_API_KEY")
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400, "month 格式 YYYY-MM")
    touched = 0
    pending = []
    for e in database.list_expenses(month=month or None):
        todo = [i for i, it in enumerate(e["items"])
                if it.get("name") and (not only_missing or not it.get("name_zh") or not it.get("sub") or "shelf_days" not in it)]
        if todo:
            pending.append((e, todo))
    remaining = max(0, len(pending) - limit)
    for e, todo in pending[:limit]:
        subset = [e["items"][i] for i in todo]
        got = await asyncio.to_thread(vision.enrich_items, subset)
        if not got:
            continue
        for i, extra in zip(todo, got):
            if not extra:
                continue
            it = e["items"][i]
            it["name_zh"] = extra["name_zh"] or it.get("name_zh", "")
            it["sub"] = it.get("sub") or extra["sub"]
            it["shelf_days"] = extra["shelf_days"] if not it.get("shelf_days") else it["shelf_days"]
        database.update_items(e["id"], e["items"])
        touched += 1
    return {"expenses_updated": touched, "remaining": remaining}


# ---- bank SMS reconcile --------------------------------------------------

class ReconcileIn(BaseModel):
    text: str


class SmsTx(BaseModel):
    date: str
    merchant: str = ""
    amount: float
    currency: str = "GBP"
    kind: str = "purchase"


class AdjustIn(BaseModel):
    expense_id: int
    tx: SmsTx


class CommitIn(BaseModel):
    transactions: list[SmsTx] = []
    adjustments: list[AdjustIn] = []


@app.post("/api/reconcile", dependencies=[Depends(require_auth)])
async def reconcile(req: ReconcileIn):
    """粘一段银行短信 → 每条的匹配结果（不落库）。"""
    txs = await asyncio.to_thread(vision.parse_sms, req.text, service.today().isoformat())
    return {"results": service.reconcile(txs)}


@app.post("/api/reconcile/image", dependencies=[Depends(require_auth)])
async def reconcile_image(file: UploadFile):
    """银行 App 流水 / 短信列表的截图 → 对账结果。"""
    if not vision.ready():
        raise HTTPException(503, "服务器没配 VISION_API_KEY")
    data = await file.read()
    try:
        parsed = await asyncio.to_thread(vision.parse_receipt, data, file.content_type or "image/jpeg",
                                         service.today().isoformat())
    except vision.VisionError as e:
        raise HTTPException(422, str(e)) from e
    if parsed.get("kind") != "transactions":
        # 单张小票也能对：当成一笔交易
        txs = [{"date": parsed["date"] or service.today().isoformat(), "merchant": parsed["merchant"],
                "amount": parsed["amount"], "currency": parsed["currency"], "kind": "purchase"}]
        return {"results": service.reconcile(txs), "single_receipt": True}
    return {"results": service.reconcile(parsed["transactions"])}


@app.post("/api/reconcile/commit", dependencies=[Depends(require_auth)])
async def reconcile_commit(req: CommitIn):
    """把勾选的未匹配交易按短信记下来。"""
    created = []
    for tx in req.transactions:
        rec = await asyncio.to_thread(service.record_sms, tx.model_dump(), vision.classify_text)
        created.append(rec)
    adjusted = []
    for a in req.adjustments:
        upd = await asyncio.to_thread(service.apply_adjust, a.expense_id, a.tx.model_dump())
        if upd:
            adjusted.append(upd)
    return {"created": created, "adjusted": adjusted}


# ---- email bills ---------------------------------------------------------

@app.post("/api/mail/poll", dependencies=[Depends(require_auth)])
async def poll_mail():
    if not mail.enabled():
        raise HTTPException(503, "服务器没配 MAIL_USER / MAIL_PASSWORD")
    try:
        result = await asyncio.to_thread(mail.poll_once)
    except Exception as e:  # noqa: BLE001
        database.set_setting("mail_last_error", str(e)[:200])
        raise HTTPException(502, f"拉取失败：{e}") from e
    return result


@app.post("/api/mail/inbound")
async def inbound_mail(request: Request, authorization: str = Header(default=""),
                       x_envelope_from: str = Header(default="")):
    """Cloudflare Email Worker 把整封原始邮件 POST 过来（message/rfc822）。
    鉴权用单独的 MAIL_INBOUND_SECRET，Worker 里不用放网站密码。"""
    if authorization.removeprefix("Bearer ").strip() != MAIL_INBOUND_SECRET:
        raise HTTPException(401, "bad secret")
    raw = await request.body()
    if not raw or len(raw) > 15 * 1024 * 1024:
        raise HTTPException(400, "empty or too large")
    status = await asyncio.to_thread(mail.handle_raw, raw, x_envelope_from)
    return {"status": status}


@app.get("/api/mail/log", dependencies=[Depends(require_auth)])
def mail_log(limit: int = 20):
    return {"log": database.list_inbound(limit)}


def _mail_status() -> dict:
    return {
        "mail_inbound": True,
        "mail_enabled": mail.enabled(),
        "mail_address": MAIL_USER,
        "mail_inbound_address": MAIL_INBOUND_ADDRESS,
        "mail_last_poll": int(database.get_setting("mail_last_poll") or 0),
        "mail_last_error": database.get_setting("mail_last_error"),
    }


def _settings_view() -> dict:
    s = database.all_settings()
    return {
        "reminder_enabled": s["reminder_enabled"] == "1",
        "reminder_hour": int(s["reminder_hour"] or 21),
        "reminder_mode": s["reminder_mode"],
        "monthly_report": s["monthly_report"] == "1",
        "budget_gbp": float(s["budget_gbp"]) if s["budget_gbp"] else None,
        "pantry_reminder": s.get("pantry_reminder", "1") == "1",
        "pantry_hour": int(s.get("pantry_hour") or 17),
        "telegram_enabled": telegram.enabled(),
        "telegram_bound": bool(telegram.bound_chat_id()),
        **_mail_status(),
    }
