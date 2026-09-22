"""Env-driven configuration, same convention as recipes/litpanel: everything
that differs between the Mac and the K3s pod comes in via environment."""
import os
from pathlib import Path

DATA_ROOT = Path(os.environ.get("DATA_ROOT", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = Path(os.environ.get("DB_PATH", DATA_ROOT / "ledger.db"))
RECEIPTS_DIR = DATA_ROOT / "receipts"      # 原始小票/截图，按 expense 关联

# 单用户站点：密码即 token。
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
# 演示实例（主页访客看的那份）：假数据、只读、不跑任何后台任务。token 固定为 guest。
DEMO_MODE = os.environ.get("DEMO_MODE", "") == "1"

# 小票/截图识别：智谱 GLM 视觉模型（OpenAI-compatible chat/completions，
# 图片走 base64 data URL）。glm-4.6v-flash 是免费档，识别复杂小票差一点。
VISION_API_KEY = os.environ.get("VISION_API_KEY", "")
VISION_API_BASE_URL = os.environ.get("VISION_API_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
VISION_MODEL = os.environ.get("VISION_MODEL", "glm-4.6v")
# 文本模型：一句话记账分类、邮件账单、银行短信。flash 免费档实测慢（60s）且漏，air 2-3s。
TEXT_MODEL = os.environ.get("TEXT_MODEL", "glm-4.5-air")

# Telegram 提醒 + 聊天记账。chat_id 可以在这里给，也可以把网站密码发给 bot 绑定。
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SITE_URL = os.environ.get("SITE_URL", "http://localhost:3070").rstrip("/")
# 可选：菜谱站地址（食材快过期时给「找菜谱」链接，按 ?q=食材 搜索）。留空就不显示。
RECIPES_URL = os.environ.get("RECIPES_URL", "").rstrip("/")
TIMEZONE = os.environ.get("TIMEZONE", "Europe/London")

HOME_CURRENCY = "GBP"     # 记账本位币
REPORT_CURRENCY = "CNY"   # 汇总同时换算

for _d in (DATA_ROOT, RECEIPTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# 邮件账单：IMAP 轮询一个邮箱（推荐专门注册一个 Gmail 收账单，用 App Password）。
# 把 Deliveroo / Uber Eats / Amazon 的邮件转发过去，或者在主邮箱设自动转发规则。
MAIL_IMAP_HOST = os.environ.get("MAIL_IMAP_HOST", "imap.gmail.com")
MAIL_IMAP_PORT = int(os.environ.get("MAIL_IMAP_PORT", "993"))
MAIL_USER = os.environ.get("MAIL_USER", "")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
MAIL_FOLDER = os.environ.get("MAIL_FOLDER", "INBOX")     # 用自己 Gmail 的话填 label 名
MAIL_POLL_SECONDS = int(os.environ.get("MAIL_POLL_SECONDS", "120"))
# 邮件正文是纯文本，用文本模型（默认跟 TEXT_MODEL）
EMAIL_MODEL = os.environ.get("EMAIL_MODEL", TEXT_MODEL)
# 邮件推送入口（Cloudflare Email Routing → Worker → POST /api/mail/inbound）。
# 不需要任何邮箱密码：把账单转发到 MAIL_INBOUND_ADDRESS 就行。留空则用 ADMIN_TOKEN。
MAIL_INBOUND_SECRET = os.environ.get("MAIL_INBOUND_SECRET", "") or ADMIN_TOKEN
# 收账单的地址，只用来在设置页上告诉你往哪儿转发（比如 ledger@你的域名）。
MAIL_INBOUND_ADDRESS = os.environ.get("MAIL_INBOUND_ADDRESS", "")
# 发件人白名单（逗号分隔，小写）。非空时只处理 From 头或信封发件人在名单里的邮件；
# 支持 "@ucl.ac.uk" 这种整域名。空 = 谁发都收。
MAIL_ALLOWED_SENDERS = [x.strip().lower() for x in os.environ.get("MAIL_ALLOWED_SENDERS", "").split(",") if x.strip()]

# 合租分账表（Google Sheet）双向同步。留空 = 关掉这个功能。
# SHEETS_SA_JSON 填服务账号 JSON 的**文件路径**，或者直接把 JSON 内容塞进来。
# 表要先分享给服务账号的邮箱（Editor）。表结构见 backend/sheets.py 开头。
SHEETS_ID = os.environ.get("SHEETS_ID", "")
SHEETS_SA_JSON = os.environ.get("SHEETS_SA_JSON", "")
SHEETS_TAB = os.environ.get("SHEETS_TAB", "明细")
SHEETS_ME = os.environ.get("SHEETS_ME", "Me")          # 我在表里叫什么
# 花名册，顺序必须和表里 E / F / G 三列的表头一致
SHEETS_PEOPLE = [x.strip() for x in os.environ.get("SHEETS_PEOPLE", "").split(",") if x.strip()]
SHEETS_SYNC_MINUTES = int(os.environ.get("SHEETS_SYNC_MINUTES", "10"))
