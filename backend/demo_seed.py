"""演示模式（DEMO_MODE=1）的假账本。访客从主页进来看到的就是这些，全部虚构。

每天第一次被访问时重建（见 app.py 的 demo 中间件），日期都相对「今天」往前排，
所以任何时候打开都像是最近在记的账。汇率直接写进缓存，不联网。"""
import random
from datetime import date, timedelta

import database
import service
from config import DB_PATH

FX_GBP_CNY = 9.62

# (商户, 分类, 明细[(英文名, 中文名, 分类, 子类, 金额, 保质期天数)])
GROCERY_BASKETS = [
    ("Tesco", [("Pak Choi 250g", "小白菜", "produce", 0.95, 4), ("Free Range Eggs x6", "散养鸡蛋 6 个", "meat", 1.85, 14),
               ("Chicken Thigh Fillets 1kg", "去骨鸡腿肉", "meat", 5.25, 3), ("Jasmine Rice 1kg", "茉莉香米", "staples", 2.10, 0),
               ("Semi Skimmed Milk 2L", "半脱脂牛奶", "dairy", 1.65, 7), ("Bananas x5", "香蕉", "produce", 0.90, 5)]),
    ("Sainsbury's", [("Tomatoes 6 pack", "番茄", "produce", 1.10, 6), ("Greek Yoghurt 500g", "希腊酸奶", "dairy", 1.95, 10),
                     ("Salmon Fillets x2", "三文鱼排", "seafood", 4.50, 2), ("Broccoli", "西兰花", "produce", 0.79, 5),
                     ("Kitchen Roll", "厨房纸", "household", 2.20, 0)]),
    ("Lidl", [("Spinach 200g", "菠菜", "produce", 0.85, 3), ("Beef Mince 500g", "牛肉末", "meat", 3.49, 3),
              ("Potatoes 2kg", "土豆", "produce", 1.19, 14), ("Oat Milk 1L", "燕麦奶", "dairy", 1.39, 10),
              ("Digestive Biscuits", "消化饼干", "snacks", 0.89, 0)]),
    ("Wing Yip", [("Light Soy Sauce 500ml", "生抽", "condiments", 2.35, 0), ("Fresh Noodles 400g", "鲜面条", "staples", 1.80, 5),
                  ("Tofu 400g", "豆腐", "produce", 1.60, 4), ("Frozen Dumplings", "速冻饺子", "staples", 4.99, 90),
                  ("Spring Onion", "小葱", "produce", 0.70, 5)]),
    ("Co-op", [("Sourdough Loaf", "酸面包", "staples", 2.40, 4), ("Cheddar 350g", "切达奶酪", "dairy", 3.15, 21),
               ("Apples x6", "苹果", "produce", 1.75, 14), ("Sparkling Water", "气泡水", "snacks", 0.95, 0)]),
]

DINING = [("Pret A Manger", "coffee", [("Oat Flat White", "燕麦馥芮白", 3.45)]),
          ("Pret A Manger", "fastfood", [("Falafel Wrap", "鹰嘴豆卷饼", 4.95), ("Americano", "美式", 2.10)]),
          ("Wagamama", "meal", [("Chicken Katsu Curry", "鸡排咖喱饭", 14.25), ("Green Tea", "绿茶", 2.95)]),
          ("Deliveroo", "delivery", [("Beef Noodle Soup", "牛肉面", 12.80), ("Delivery fee", "配送费", 2.49)]),
          ("Kiss the Hippo", "coffee", [("Cortado", "可塔朵", 3.60)]),
          ("Leon", "fastfood", [("Chicken Box", "鸡肉饭盒", 8.95)]),
          ("Dishoom", "meal", [("Black Daal", "黑扁豆咖喱", 9.50), ("Chai", "印度奶茶", 3.90), ("Naan", "馕", 3.20)]),
          ("UCL Refectory", "meal", [("Lunch Set", "午餐套餐", 5.40)])]

TRANSPORT = [("TfL", "public", "Tube / bus daily cap", "地铁公交日封顶", 8.90),
             ("TfL", "public", "Bus fare", "公交", 1.75),
             ("Lime", "bike", "E-bike ride", "电单车", 3.20),
             ("Uber", "taxi", "Ride home", "打车回家", 14.60)]


def _fx_cache(days: list[str]):
    for d in days:
        database.set_fx(d, "GBPCNY", FX_GBP_CNY)
        database.set_fx(d, "CNYGBP", round(1 / FX_GBP_CNY, 6))


def _add(day: date, merchant: str, category: str, items: list[dict], currency: str = "GBP",
         note: str = "", source: str = "manual") -> int:
    total = round(sum(i["amount"] for i in items), 2)
    rec = service.build_expense({"date": day.isoformat(), "merchant": merchant, "category": category,
                                 "currency": currency, "amount": total, "items": items, "note": note}, source=source)
    return database.insert_expense(rec)


def _item(name, zh, category, sub, amount, shelf=0, qty=1):
    return {"name": name, "name_zh": zh, "category": category, "sub": sub, "amount": amount,
            "shelf_days": shelf, "qty": qty}


def rebuild():
    """清空演示库重新生成。只在 DEMO_MODE 下被调用。"""
    for suffix in ("", "-wal", "-shm"):
        p = DB_PATH.with_name(DB_PATH.name + suffix)
        if p.exists():
            p.unlink()
    database.init_db()

    today = service.today()
    rng = random.Random(today.toordinal())
    first = today.replace(day=1)
    start = (first - timedelta(days=40)).replace(day=1)       # 上上个月 1 号起，三个完整月份
    days = [start + timedelta(days=i) for i in range((today - start).days + 1)]
    _fx_cache([d.isoformat() for d in days])

    # 几条日程提醒，日历上能看到样子
    for delta, hm, text in ((1, "11:00", "吃饭带小礼物"), (3, "09:00", "交 dissertation 提纲"), (-2, "18:30", "给家里打电话")):
        rid = database.add_reminder(f"{(today + timedelta(days=delta)).isoformat()} {hm}", text)
        if delta < 0:
            database.update_reminder(rid, status="sent")

    for d in days:
        # 房租、订阅：每月固定
        if d.day == 1:
            _add(d, "Unite Students", "housing", [_item("Monthly rent", "房租", "housing", "rent", 1180.00)])
            _add(d, "giffgaff", "subscription", [_item("Golden goodybag 20GB", "流量套餐", "subscription", "phone", 10.00)])
        if d.day == 12:
            _add(d, "Spotify", "subscription", [_item("Premium Student", "学生会员", "subscription", "streaming", 5.99)])
        if d.day == 20:
            _add(d, "Octopus Energy", "housing", [_item("Electricity", "电费", "housing", "utilities", 38.40)])

        # 平时：隔两三天买次菜，几乎每天一杯咖啡或一顿饭，通勤
        if rng.random() < 0.38:
            shop, basket = rng.choice(GROCERY_BASKETS)
            picked = rng.sample(basket, k=rng.randint(3, len(basket)))
            items = [_item(n, zh, "groceries", sub, amt, shelf) for n, zh, sub, amt, shelf in picked]
            items.append(_item("Carrier bag", "购物袋", "groceries", "fees", 0.10))
            _add(d, shop, "groceries", items, source=rng.choice(["receipt", "manual", "telegram"]))
        if rng.random() < 0.7:
            shop, sub, lines = rng.choice(DINING)
            _add(d, shop, "dining", [_item(n, zh, "dining", sub, amt) for n, zh, amt in lines],
                 source=rng.choice(["manual", "telegram"]))
        if d.weekday() < 5 and rng.random() < 0.75:
            shop, sub, n, zh, amt = rng.choice(TRANSPORT[:2])
            _add(d, shop, "transport", [_item(n, zh, "transport", sub, amt)])
        elif rng.random() < 0.15:
            shop, sub, n, zh, amt = rng.choice(TRANSPORT[2:])
            _add(d, shop, "transport", [_item(n, zh, "transport", sub, amt)])

    # 偶尔的大件和人民币消费
    def ago(n: int) -> date:
        return today - timedelta(days=n)

    _add(ago(80), "Waterstones", "study", [_item("Mostly Harmless Econometrics", "基本无害的计量经济学", "study", "books", 24.99)])
    _add(ago(66), "Uniqlo", "shopping", [_item("Ultra Light Down Jacket", "轻羽绒服", "shopping", "clothes", 59.90)])
    _add(ago(52), "Trainline", "travel", [_item("London → Edinburgh return", "伦敦—爱丁堡往返", "travel", "flights", 86.40)],
         note="周末去爱丁堡")
    _add(ago(51), "Premier Inn", "travel", [_item("1 night", "住一晚", "travel", "hotel", 72.00)])
    _add(ago(40), "淘宝", "shopping", [_item("Phone case", "手机壳", "shopping", "electronics", 39.00),
                                     _item("Hair clips", "发夹", "shopping", "beauty", 25.00)], currency="CNY",
         note="寄到国内家里")
    _add(ago(33), "Boots", "health", [_item("Paracetamol", "扑热息痛", "health", "medicine", 0.69),
                                      _item("Vitamin D", "维生素 D", "health", "medicine", 4.50)])
    _add(ago(25), "Barbican", "entertainment", [_item("Concert ticket", "音乐会门票", "entertainment", "shows", 28.00)])
    _add(ago(17), "Rymans", "study", [_item("A4 notebooks x3", "笔记本", "study", "stationery", 7.50),
                                      _item("Printing", "打印", "study", "stationery", 3.20)])
    _add(ago(9), "PureGym", "health", [_item("Monthly membership", "健身房月卡", "health", "fitness", 24.99)])
    _add(ago(4), "美团", "dining", [_item("Takeaway for parents", "给爸妈点的外卖", "dining", "delivery", 168.00)],
         currency="CNY")

    # 分账 / 代付
    sid = _add(ago(6), "Tesco", "groceries", [
        _item("Toilet Roll 9 pack", "卷纸", "groceries", "household", 4.75),
        _item("Washing Up Liquid", "洗洁精", "groceries", "household", 1.25),
        _item("Olive Oil 1L", "橄榄油", "groceries", "condiments", 6.50, 365)], note="公共用品")
    database.set_split(sid, "split", 3, "Mia, Leo", False)
    sid = _add(ago(13), "Dishoom", "dining", [_item("Dinner for three", "三人晚饭", "dining", "meal", 71.40)],
               note="和同学聚餐")
    database.set_split(sid, "split", 3, "Mia, Leo", True)
    sid = _add(ago(3), "Amazon", "shopping", [_item("Desk lamp", "台灯", "shopping", "home", 22.99)],
               note="帮 Leo 买的", source="manual")
    database.set_split(sid, "paid_for", 1, "Leo", False)

    # 库存：早买的食材大多已经吃完或扔掉，只留最近一周的在冰箱里
    for p in database.list_pantry():
        if date.fromisoformat(p["bought"]) < today - timedelta(days=7):
            database.set_pantry_status(p["id"], "tossed" if rng.random() < 0.1 else "used")

    database.set_setting("budget_gbp", "1800")
    database.set_setting("demo_seeded", today.isoformat())
