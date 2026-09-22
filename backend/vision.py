"""小票/截图 → 结构化消费（智谱 GLM 视觉模型），以及一句话记账的文本分类。
两个都是 OpenAI-compatible chat/completions，共用一个 key。"""
import base64
import json
import re

import httpx

from categories import category_menu, coerce_category, coerce_sub, guess_category
from config import EMAIL_MODEL, TEXT_MODEL, VISION_API_BASE_URL, VISION_API_KEY, VISION_MODEL

RECEIPT_PROMPT = """你是一个记账助手。先判断这张图片是哪一种：

A. **一笔消费的凭证**：购物小票、账单、订单详情页、单笔支付成功截图（英国超市、餐厅、Deliveroo、Uber、
   Trainline、Monzo、Revolut、Apple Pay，或支付宝/微信）
B. **多笔交易的列表**：银行/信用卡 App 的交易流水、消费通知短信的截图、账单明细页（一屏好几笔、每笔一个商家一个金额）

如果是 B，严格输出：
{"kind": "transactions", "transactions": [
  {"date": "YYYY-MM-DD（没有年份按今天 {today} 推算最近的过去日期；没有就 null）", "merchant": "商家（照抄）",
   "amount": 数字, "currency": "GBP / CNY / EUR / USD", "kind": "purchase | refund | other（还款/入账/转账）"}
]}
（每笔一条，pending 的也算；退款、还款 kind 填 refund / other）

如果是 A，提取这笔消费的信息，严格输出一个 JSON 对象（不要 markdown 代码块，不要多余文字）：
{
  "kind": "receipt",
  "merchant": "商家名（简短规范，如 Tesco / Sainsbury's / Pret A Manger / Deliveroo·Wagamama）",
  "date": "消费日期 YYYY-MM-DD；图上没有就填 null（英国小票日期通常是 DD/MM/YYYY）",
  "currency": "货币 ISO 代码：£ 是 GBP，¥/￥ 是 CNY，€ 是 EUR，$ 是 USD",
  "total": 实付总金额（数字；扣掉折扣、优惠券后真正付的钱），
  "category": "整笔的主分类 key",
  "items": [
    {"name": "商品/项目名（保留原文，太长可缩短）", "name_zh": "中文通俗叫法：只写东西本身，不要音译超市/品牌名（WR、Tesco、Clarence Court 这类去掉或保留英文），如 WR CHICKEN BRSTS → 鸡胸肉，TSCO SKMD MILK 2PT → 脱脂牛奶 2 品脱，Clarence Court Burford Brown Eggs → Burford Brown 鸡蛋；本来就是中文照抄", "qty": 数量（默认 1）, "amount": 该行实付金额（数字，已含折扣、是单价×数量之后的金额）, "category": "该商品的分类 key", "sub": "该分类下的子类 key，没合适的填 ''", "shelf_days": 冷藏可存放天数（生鲜肉 3、鱼虾 2、绿叶菜 4、根茎 14、奶 7、鸡蛋 21、米面油罐头 180；不是食物、或者零食饮料日用品填 0）}
  ],
  "order_ref": "订单号 / 交易号 / 小票号，没有就 ''",
  "note": "值得记的备注，比如 '用了 Clubcard 省了 2.30' / 'Meal deal'；没有就填 ''"
}

分类 key 只能从下面选：
{category_menu}

规则：
- items 只列真正的商品/服务行；Subtotal / VAT / Total / Change / Cash / Card / 积分 / 折扣行都不算商品，折扣直接减到对应商品上（减不到对应商品就只体现在 total 里）
- 支付截图（只有一个金额没有明细）时 items 就一条，name 用商家或用途
- 每个商品都要给 category 和 sub：超市里的洗发水是 groceries/toiletries，牛奶是 groceries/dairy，鸡胸肉是 groceries/meat；外卖里的餐费是 dining/delivery，配送费是 dining/fees
- 数字只写数字不带货币符号，保留两位小数
- 看不到某一行的金额时该项 amount 填 null；绝对不要把总额塞进第一项，也不要平均分
- 商品名照抄图上文字；文字被截断或只有图片时写简短描述即可，不要加「推测」「因图片中…」之类的说明
- 订单页 / 支付截图上如果有下单日期，一定填进 date（不要用截图时间）
- 如果图片完全看不出任何消费金额，输出 {"error": "看不出这是一笔消费"}"""

TEXT_PROMPT = """用户用一句话记账，我已经把金额拆出来了，剩下的描述是：「{text}」
请判断商家/用途和分类，严格输出 JSON（不要 markdown）：
{"merchant": "商家或用途（简短，保留用户原话里的名字，如 Tesco / 午饭 / Uber）", "category": "分类 key", "sub": "子类 key 或 ''", "note": ""}

分类 key 只能从下面选：
{category_menu}"""


class VisionError(RuntimeError):
    pass


def ready() -> bool:
    return bool(VISION_API_KEY)


def _chat(model: str, content, temperature: float = 0.1, timeout: int = 120) -> str:
    if not VISION_API_KEY:
        raise VisionError("VISION_API_KEY 未配置，识别功能不可用")
    body = {
        "model": model,
        "temperature": temperature,
        "messages": [{"role": "user", "content": content}],
        # GLM-4.5+ 默认开思考，小票抽取不需要，关掉更快更省
        "thinking": {"type": "disabled"},
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{VISION_API_BASE_URL}/chat/completions",
                           headers={"Authorization": f"Bearer {VISION_API_KEY}"}, json=body)
    if resp.status_code != 200:
        raise VisionError(f"模型 {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"] or ""


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise VisionError(f"模型没有返回合法 JSON：{text[:200]}") from e


def _num(v, default=0.0) -> float:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v).replace(",", ""))
    return float(m.group(0).replace(",", ".")) if m else default


def parse_receipt(image_bytes: bytes, mime: str = "image/jpeg", today: str = "") -> dict:
    """一张图 → {"kind": "receipt", ...一笔消费} 或 {"kind": "transactions", "transactions": [...]}（银行流水截图）。"""
    b64 = base64.b64encode(image_bytes).decode()
    prompt = RECEIPT_PROMPT.replace("{category_menu}", category_menu()).replace("{today}", today or _today())
    content = [
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        {"type": "text", "text": prompt},
    ]
    data = _extract_json(_chat(VISION_MODEL, content))
    if data.get("error"):
        raise VisionError(str(data["error"]))
    if data.get("kind") == "transactions" or (isinstance(data.get("transactions"), list) and not data.get("total")):
        txs = normalize_transactions(data.get("transactions") or [], today or _today())
        if not txs:
            raise VisionError("看起来是流水截图，但没读出任何交易")
        return {"kind": "transactions", "transactions": txs}
    parsed = normalize_parsed(data)
    if parsed["date"]:
        parsed["date"] = _sane_date(parsed["date"], today or _today(), max_age_days=730)
    parsed["kind"] = "receipt"
    return parsed


def _today() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def normalize_parsed(data: dict) -> dict:
    total = round(abs(_num(data.get("total"))), 2)
    category = coerce_category(data.get("category"))
    items = []
    for raw in data.get("items") or []:
        if isinstance(raw, str):
            raw = {"name": raw}
        name = str(raw.get("name") or "").strip()[:80]
        amount = round(abs(_num(raw.get("amount"))), 2)
        if not name and not amount:
            continue
        qty = _num(raw.get("qty"), 1) or 1
        icat = coerce_category(raw.get("category") or category)
        items.append({"name": name or "（未命名）", "name_zh": str(raw.get("name_zh") or "").strip()[:40],
                      "qty": qty, "amount": amount,
                      "category": icat, "sub": coerce_sub(icat, raw.get("sub")),
                      "shelf_days": int(max(0, min(730, _num(raw.get("shelf_days"), 0))))})
    # 模型有时把总额塞给第一项、其余 0 —— 这说明它其实没看到单项价格，全部清空按整笔记
    priced = [i for i in items if i["amount"] > 0]
    if len(items) >= 2 and len(priced) == 1 and total and abs(priced[0]["amount"] - total) < 0.011:
        for i in items:
            i["amount"] = 0.0
    if not total and items:
        total = round(sum(i["amount"] for i in items), 2)
    if not total:
        raise VisionError("没识别出金额，换张清楚点的图或者手动填")
    date = _coerce_date(data.get("date"))
    currency = _coerce_currency(data.get("currency"))
    return {
        "merchant": str(data.get("merchant") or "").strip()[:60],
        "date": date,
        "currency": currency,
        "amount": total,
        "category": category if category != "other" or not items else items[0]["category"],
        "items": items,
        "note": str(data.get("note") or "").strip()[:200],
        "order_ref": re.sub(r"[^\w-]", "", str(data.get("order_ref") or ""))[:80],
    }


_SYMBOLS = {"£": "GBP", "¥": "CNY", "￥": "CNY", "€": "EUR", "$": "USD", "RMB": "CNY"}


def _coerce_currency(value) -> str:
    v = str(value or "GBP").strip().upper()
    v = _SYMBOLS.get(v, v)
    return v if re.fullmatch(r"[A-Z]{3}", v) else "GBP"


def _coerce_date(value) -> str:
    """模型偶尔照抄小票上的 DD/MM/YYYY，这里兜一下；认不出就留空让人填。"""
    v = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", v)
    if m:
        d, mo, y = m.groups()
        y = f"20{y}" if len(y) == 2 else y
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return ""


def classify_text(description: str) -> dict:
    """一句话记账里的描述 → {merchant, category}。没有 key 或失败就关键词兜底。"""
    fallback = {"merchant": description.strip()[:60], "category": guess_category(description), "sub": "", "note": ""}
    if not VISION_API_KEY or not description.strip():
        return fallback
    try:
        prompt = TEXT_PROMPT.replace("{text}", description.strip()[:200]).replace("{category_menu}", category_menu())
        data = _extract_json(_chat(TEXT_MODEL, prompt, timeout=30))
        cat = coerce_category(data.get("category") or fallback["category"])
        return {
            "merchant": str(data.get("merchant") or fallback["merchant"]).strip()[:60],
            "category": cat,
            "sub": coerce_sub(cat, data.get("sub")),
            "note": str(data.get("note") or "").strip()[:200],
        }
    except Exception:  # noqa: BLE001
        return fallback


# ---- email bills -----------------------------------------------------------

EMAIL_PROMPT = """你是一个记账助手。下面是一封转发来的邮件，可能是 Deliveroo / Uber Eats / Just Eat 的订单账单、
Amazon 订单确认、Trainline 车票、Uber 行程收据、房租/水电/话费账单，或者别的消费凭证。
请提取这笔消费，严格输出一个 JSON 对象（不要 markdown 代码块，不要多余文字）：
{
  "merchant": "商家（外卖写 '平台 · 餐厅名'，如 'Deliveroo · Wagamama'；网购写店名）",
  "date": "下单/消费日期 YYYY-MM-DD；找不到就填 null",
  "currency": "GBP / CNY / EUR / USD",
  "total": 实付总额（数字：含配送费、服务费、小费，扣掉优惠码/积分后真正付的钱），
  "category": "主分类 key",
  "items": [
    {"name": "菜品/商品名", "name_zh": "中文通俗叫法：只写东西本身，不要音译超市/品牌名（WR、Tesco、Clarence Court 这类去掉或保留英文），如 WR CHICKEN BRSTS → 鸡胸肉，TSCO SKMD MILK 2PT → 脱脂牛奶 2 品脱，Clarence Court Burford Brown Eggs → Burford Brown 鸡蛋；本来就是中文照抄", "qty": 数量, "amount": 该行实付金额（数字）, "category": "分类 key", "sub": "子类 key，没合适的填 ''", "shelf_days": 冷藏可存放天数（生鲜肉 3、鱼虾 2、绿叶菜 4、根茎 14、奶 7、鸡蛋 21、米面油 180；外卖菜品、非食物、零食饮料填 0）}
  ],
  "order_ref": "订单号 / 交易号，没有就 ''",
  "note": "值得记的备注（用了什么优惠、退款等），没有就 ''"
}

分类 key 只能从下面选：
{category_menu}

规则：
- 只认真正付了钱的账单。营销邮件、配送状态更新（rider on the way / delivered 且没有金额明细）、退款通知、
  验证码之类的，输出 {"error": "不是账单"}
- 转发邮件的头部（From: / Sent: / To: / Subject:）忽略，日期用订单日期不用转发日期
- 配送费 / 服务费 / 小费各作为单独一行 item，category 和主分类一样、sub 填 fees；优惠折扣不作为 item，直接体现在 total
- 外卖 category 是 dining；火车/公交/Uber 是 transport；Amazon 按买的东西判断
- 邮件里只有商品名没有每项价格时（很多订单确认邮件都这样），items 的 amount 全部填 null，
  绝对不要把总额塞进第一项，也不要平均分；总额照常填 total
- 数字只写数字不带货币符号

邮件：
---
{email}
---"""


def parse_email(text: str) -> dict:
    prompt = EMAIL_PROMPT.replace("{category_menu}", category_menu()).replace("{email}", text[:14000])
    data = _extract_json(_chat(EMAIL_MODEL, prompt, timeout=90))
    if data.get("error"):
        raise VisionError(str(data["error"]))
    return normalize_parsed(data)


# ---- bank SMS ------------------------------------------------------------

SMS_PROMPT = """下面是用户粘贴的银行 / 信用卡消费通知（可能好几条，可能来自英国银行 Monzo / Barclays / HSBC /
Lloyds / Amex / Chase / Revolut，也可能是中国银行的中文短信）。把每一笔抽出来，严格输出 JSON（不要 markdown）：
{"transactions": [
  {"date": "YYYY-MM-DD，没有年份就按今天 {today} 推算最近的过去日期；完全没有就 null",
   "merchant": "商家（照抄短信里的名字，如 TESCO STORES 3021 / DELIVEROO）",
   "amount": 数字, "currency": "GBP / CNY / EUR / USD",
   "kind": "purchase | refund | other（还款、入账、转账、验证码等）"}
]}
金额只写数字。一条短信一笔；没有任何消费就输出 {"transactions": []}。

短信：
---
{sms}
---"""

_SMS_LINE = re.compile(r"(?:£|GBP\s?)(\d+(?:[.,]\d{1,2})?)", re.I)


def _sane_date(value: str, today: str, max_age_days: int = 300) -> str:
    """截图/短信上常常没有年份，模型有时猜错年：落在未来或太久以前的，年份换成今年
    （换完还在未来就再减一年，比如 1 月对账 12 月的账）。"""
    if not value:
        return today
    from datetime import date as _date
    try:
        d, t = _date.fromisoformat(value), _date.fromisoformat(today)
    except ValueError:
        return today
    if d > t or (t - d).days > max_age_days:
        try:
            d = d.replace(year=t.year)
        except ValueError:          # 2 月 29 日
            d = d.replace(year=t.year, day=28)
        if d > t:
            d = d.replace(year=t.year - 1)
    return d.isoformat()


def normalize_transactions(rows: list, today: str) -> list[dict]:
    out = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        amount = round(abs(_num(raw.get("amount"))), 2)
        if not amount:
            continue
        out.append({
            "date": _sane_date(_coerce_date(raw.get("date")), today),
            "merchant": str(raw.get("merchant") or "").strip()[:60],
            "amount": amount,
            "currency": _coerce_currency(raw.get("currency")),
            "kind": raw.get("kind") if raw.get("kind") in ("purchase", "refund", "other") else "purchase",
        })
    return out


def parse_sms(text: str, today: str) -> list[dict]:
    """银行短信 → [{date, merchant, amount, currency, kind}]。没 key 就用正则兜底（只抓 £ 金额）。"""
    out: list[dict] = []
    if VISION_API_KEY and text.strip():
        try:
            prompt = SMS_PROMPT.replace("{today}", today).replace("{sms}", text.strip()[:8000])
            data = _extract_json(_chat(TEXT_MODEL, prompt, timeout=60))
            return normalize_transactions(data.get("transactions") or [], today)
        except Exception:  # noqa: BLE001
            pass
    for line in text.splitlines():
        m = _SMS_LINE.search(line)
        if m:
            out.append({"date": today, "merchant": re.sub(r"\s+", " ", line[m.end():].strip(" .,;:at"))[:60],
                        "amount": float(m.group(1).replace(",", ".")), "currency": "GBP", "kind": "purchase"})
    return out


# ---- backfill: 中文名 / 子类 / 保质期 -------------------------------------

ENRICH_PROMPT = """下面是超市小票 / 订单上的商品名（英国超市常见缩写，如 WR = Waitrose 自有品牌，TSCO = Tesco，BRSTS = breasts）。
给每一项补三个字段，严格输出 JSON（不要 markdown）：
{"items": [{"i": 序号, "name_zh": "中文通俗叫法：只写东西本身，不要音译超市/品牌名（WR、Tesco、Clarence Court 这类去掉或保留英文），如 WR CHICKEN BRSTS → 鸡胸肉，TSCO SKMD MILK 2PT → 脱脂牛奶 2 品脱，Clarence Court Burford Brown Eggs → Burford Brown 鸡蛋；本来就是中文照抄", "sub": "子类 key 或 ''", "shelf_days": 冷藏可存放天数}]}
shelf_days：生鲜肉 3、鱼虾 2、绿叶菜 4、根茎/瓜果 14、奶 7、鸡蛋 21、米面油罐头 180；不是食物或零食饮料日用品填 0。
子类 key 从这里选（按商品所属的大类）：
{category_menu}

商品：
{items}"""


def enrich_items(items: list[dict]) -> list[dict]:
    """[{name, category}] → [{name_zh, sub, shelf_days}]，顺序对齐。失败返回空表。"""
    if not VISION_API_KEY or not items:
        return []
    listing = "\n".join(f"{i + 1}. [{it.get('category', 'other')}] {it['name']}" for i, it in enumerate(items))
    try:
        prompt = ENRICH_PROMPT.replace("{category_menu}", category_menu()).replace("{items}", listing[:8000])
        data = _extract_json(_chat(TEXT_MODEL, prompt, timeout=90))
    except Exception:  # noqa: BLE001
        return []
    out = [{} for _ in items]
    for row in data.get("items") or []:
        try:
            k = int(row.get("i")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= k < len(items):
            cat = items[k].get("category", "other")
            out[k] = {"name_zh": str(row.get("name_zh") or "").strip()[:40],
                      "sub": coerce_sub(cat, row.get("sub")),
                      "shelf_days": int(max(0, min(730, _num(row.get("shelf_days"), 0))))}
    return out
