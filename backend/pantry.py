"""食材库存：买菜明细里能坏的食物（shelf_days > 0）自动进库存，按买入日 + 保质期
算过期日。用完 / 扔了手动标；快过期的由 scheduler 推 Telegram 提醒做菜。"""
from datetime import date, timedelta

import database
from config import RECIPES_URL, SITE_URL

PANTRY_SUBS = {"produce", "meat", "seafood", "dairy", "staples", "condiments"}
FALLBACK_DAYS = {"produce": 5, "meat": 3, "seafood": 2, "dairy": 7, "staples": 180, "condiments": 365}


def sync_expense(expense_id: int, data: dict):
    """入库 / 改动一笔后同步：新食材加进去，删掉的移除，已有的保留状态。"""
    keep = []
    if data.get("category") == "groceries" or any(i.get("category") == "groceries" for i in data.get("items") or []):
        for idx, it in enumerate(data.get("items") or []):
            if it.get("category", data.get("category")) != "groceries" or it.get("who") == "them":
                continue
            days = int(it.get("shelf_days") or 0) or FALLBACK_DAYS.get(it.get("sub") or "", 0)
            if days <= 0 or (it.get("sub") and it["sub"] not in PANTRY_SUBS):
                continue
            keep.append(idx)
            database.upsert_pantry(expense_id, idx, it.get("name_zh") or it["name"], it["name"], it.get("sub") or "",
                                   float(it.get("qty") or 1), data["date"],
                                   (date.fromisoformat(data["date"]) + timedelta(days=days)).isoformat())
    database.prune_pantry(expense_id, keep)


def overview(today: date) -> dict:
    rows = database.list_pantry()
    for r in rows:
        r["days_left"] = (date.fromisoformat(r["expires"]) - today).days
    soon = [r for r in rows if r["days_left"] <= 2]
    fresh = [r for r in rows if 2 < r["days_left"] <= 30]
    dry = [r for r in rows if r["days_left"] > 30]
    return {"soon": soon, "fresh": fresh, "dry": dry, "count": len(rows)}


def search_key(name: str) -> str:
    """「菠菜 220g」→「菠菜」：去掉数量/规格，拿去菜谱站搜。"""
    import re
    cjk = re.findall(r"[\u4e00-\u9fff]+", name)          # 「Burford Brown 鸡蛋」→ 鸡蛋
    if cjk:
        return max(cjk, key=len)[:6]
    return re.split(r"[\d（(\s]", name.strip())[0][:6] or name[:4]


def expiring_text(today: date) -> str:
    ov = overview(today)
    if not ov["soon"]:
        return ""
    lines = ["🥬 冰箱里这些快到期了，今晚做菜？", ""]
    for r in ov["soon"]:
        left = r["days_left"]
        when = "今天" if left == 0 else "明天" if left == 1 else f"{left} 天内" if left > 0 else f"已过期 {-left} 天"
        label = f"{r['name']} · {r['name_raw']}" if r["name_raw"] and r["name_raw"] != r["name"] else r["name"]
        lines.append(f"• {label}（{r['bought'][5:]} 买，{when}）")
    if ov["fresh"]:
        lines.append("")
        lines.append("还有：" + "、".join(r["name"] for r in ov["fresh"][:8]) + ("…" if len(ov["fresh"]) > 8 else ""))
    recipes = f' · <a href="{RECIPES_URL}/?q={search_key(ov["soon"][0]["name"])}">找菜谱</a>' if RECIPES_URL else ""
    lines.append(f'\n<a href="{SITE_URL}/pantry">看库存</a>{recipes} · /used 1 2 标用完')
    return "\n".join(lines)
