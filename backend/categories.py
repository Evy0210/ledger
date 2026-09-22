"""消费分类。key 存库，label 给人看；前端 types.ts 里有一份镜像。"""
import re

CATEGORIES = [
    {"key": "groceries",     "label": "超市买菜", "emoji": "🛒", "hint": "超市、便利店、菜场、日用品"},
    {"key": "dining",        "label": "外食餐饮", "emoji": "🍜", "hint": "餐厅、外卖、咖啡、奶茶、酒吧"},
    {"key": "transport",     "label": "交通出行", "emoji": "🚌", "hint": "公交、地铁、火车、打车、自行车、油费"},
    {"key": "housing",       "label": "房租水电", "emoji": "🏠", "hint": "房租、水电燃气、网费、council tax"},
    {"key": "study",         "label": "学习教育", "emoji": "📚", "hint": "学费、教材、文具、课程、打印"},
    {"key": "shopping",      "label": "购物",     "emoji": "🛍️", "hint": "衣服、鞋包、数码、家居、化妆品"},
    {"key": "entertainment", "label": "娱乐社交", "emoji": "🎬", "hint": "电影、演出、游戏、聚会、礼物"},
    {"key": "health",        "label": "健康医疗", "emoji": "💊", "hint": "药、看病、健身、保险"},
    {"key": "travel",        "label": "旅行",     "emoji": "✈️", "hint": "机票、酒店、旅行中的花销"},
    {"key": "subscription",  "label": "订阅通讯", "emoji": "📱", "hint": "手机套餐、Netflix/Spotify、软件、会员"},
    {"key": "other",         "label": "其他",     "emoji": "📦", "hint": "不好归类的"},
]
CATEGORY_KEYS = [c["key"] for c in CATEGORIES]
CATEGORY_MAP = {c["key"]: c for c in CATEGORIES}

# 二级分类：明细每一项可以再细分。前端 types.ts 里有一份镜像。
SUBCATEGORIES: dict[str, list[tuple[str, str]]] = {
    "groceries": [("produce", "蔬菜水果"), ("meat", "肉禽蛋"), ("seafood", "海鲜"), ("dairy", "奶制品"),
                  ("staples", "主食粮油"), ("snacks", "零食饮料"), ("condiments", "调料"),
                  ("household", "日用品"), ("toiletries", "洗护"), ("fees", "购物袋/杂费")],
    "dining": [("meal", "正餐"), ("delivery", "外卖"), ("coffee", "咖啡奶茶"), ("fastfood", "快餐小吃"),
               ("drinks", "酒吧饮酒"), ("fees", "配送/服务费")],
    "transport": [("public", "公交地铁"), ("rail", "火车"), ("taxi", "打车"), ("bike", "共享单车"), ("other", "其他")],
    "housing": [("rent", "房租"), ("utilities", "水电燃气"), ("internet", "网费"), ("council", "Council Tax"), ("other", "其他")],
    "study": [("tuition", "学费"), ("books", "书籍教材"), ("stationery", "文具打印"), ("courses", "课程考试"), ("other", "其他")],
    "shopping": [("clothes", "衣服鞋包"), ("electronics", "数码"), ("home", "家居"), ("beauty", "美妆护肤"),
                 ("gifts", "礼物"), ("other", "其他")],
    "entertainment": [("shows", "电影演出"), ("games", "游戏"), ("social", "聚会社交"), ("sports", "运动"), ("other", "其他")],
    "health": [("medicine", "药品"), ("medical", "看病诊疗"), ("fitness", "健身"), ("insurance", "保险"), ("other", "其他")],
    "travel": [("flights", "机票"), ("hotel", "住宿"), ("local", "当地交通"), ("attractions", "景点门票"),
               ("food", "旅途餐饮"), ("other", "其他")],
    "subscription": [("phone", "话费流量"), ("streaming", "流媒体"), ("software", "软件"), ("membership", "会员"), ("other", "其他")],
    "other": [],
}
SUB_LABEL = {(cat, key): label for cat, subs in SUBCATEGORIES.items() for key, label in subs}


def coerce_sub(category: str, sub) -> str:
    v = str(sub or "").strip().lower()
    return v if (category, v) in SUB_LABEL else ""


def coerce_category(value) -> str:
    v = str(value or "").strip().lower()
    return v if v in CATEGORY_MAP else "other"


def category_menu() -> str:
    lines = []
    for c in CATEGORIES:
        subs = ", ".join(f"{k}={label}" for k, label in SUBCATEGORIES[c["key"]])
        lines.append(f"- {c['key']}：{c['label']}（{c['hint']}）" + (f"；子类 sub 可选：{subs}" if subs else ""))
    return "\n".join(lines)


# 没有 LLM 时（或 LLM 挂了）给 Telegram 一句话记账兜底的关键词表。
_KEYWORDS: list[tuple[str, str]] = [
    ("groceries", r"tesco|sainsbury|lidl|aldi|asda|morrisons|waitrose|co-?op|m&s|marks|iceland|超市|买菜|菜|water|milk|egg|bread|fruit|veg"),
    ("dining", r"coffee|cafe|caf[eé]|pret|costa|starbucks|greggs|nando|kfc|mcdonald|burger|pizza|deliveroo|uber ?eats|just ?eat|lunch|dinner|breakfast|restaurant|pub|beer|奶茶|咖啡|外卖|午饭|晚饭|早饭|吃饭|饭|餐"),
    ("transport", r"bus|tube|train|rail|oyster|tfl|uber|bolt|taxi|lime|bike|地铁|公交|火车|打车|出租|巴士|车票"),
    ("housing", r"rent|deposit|council|electric|gas bill|water bill|wifi|broadband|房租|水电|网费|燃气"),
    ("study", r"book|textbook|stationery|print|course|tuition|学费|书|文具|打印|课程"),
    ("health", r"boots|pharmacy|gp|dentist|gym|medicine|paracetamol|药|医|健身"),
    ("subscription", r"netflix|spotify|apple|icloud|giffgaff|ee\b|vodafone|three|o2|sim|会员|订阅|话费|套餐"),
    ("travel", r"flight|ryanair|easyjet|hotel|airbnb|hostel|机票|酒店|民宿|旅行"),
    ("entertainment", r"cinema|movie|film|ticket|concert|game|steam|gift|电影|演出|游戏|礼物|聚会"),
    ("shopping", r"primark|zara|uniqlo|h&m|amazon|argos|ikea|john lewis|clothes|shoes|衣服|鞋|包|数码|家居"),
]


def guess_category(text: str) -> str:
    t = (text or "").lower()
    for key, pattern in _KEYWORDS:
        if re.search(pattern, t):
            return key
    return "other"
