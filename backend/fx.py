"""汇率：frankfurter.dev（欧洲央行参考汇率，免费无 key，有历史），兜底
open.er-api.com（只有当天），再兜底最近一次缓存。每天每个币对只查一次。"""
import logging

import httpx

import database

log = logging.getLogger("ledger.fx")
FALLBACK = {"GBPCNY": 9.4, "EURGBP": 0.85, "USDGBP": 0.78, "CNYGBP": 0.106}


def get_rate(day: str, base: str, quote: str) -> float:
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return 1.0
    pair = base + quote
    cached = database.get_fx(day, pair)
    if cached:
        return cached
    rate = _fetch_frankfurter(day, base, quote) or _fetch_erapi(base, quote)
    if rate:
        database.set_fx(day, pair, rate)
        return rate
    latest = database.latest_fx(pair)
    if latest:
        return latest
    inverse = database.latest_fx(quote + base)
    if inverse:
        return 1 / inverse
    log.warning("no fx for %s, using hardcoded fallback", pair)
    return FALLBACK.get(pair) or (1 / FALLBACK[quote + base] if quote + base in FALLBACK else 1.0)


def _fetch_frankfurter(day: str, base: str, quote: str) -> float | None:
    try:
        with httpx.Client(timeout=10) as client:
            # 日期在未来/周末时 frankfurter 自动回退到最近的交易日
            resp = client.get(f"https://api.frankfurter.dev/v1/{day}", params={"base": base, "symbols": quote})
        if resp.status_code == 200:
            return float(resp.json()["rates"][quote])
    except Exception as e:  # noqa: BLE001
        log.info("frankfurter failed: %s", e)
    return None


def _fetch_erapi(base: str, quote: str) -> float | None:
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"https://open.er-api.com/v6/latest/{base}")
        if resp.status_code == 200:
            return float(resp.json()["rates"][quote])
    except Exception as e:  # noqa: BLE001
        log.info("er-api failed: %s", e)
    return None
