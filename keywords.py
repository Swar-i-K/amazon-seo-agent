"""
keywords.py
-----------
Free keyword ideas and seasonality. No paid SEO tool, so there is NO search
volume and NO difficulty score here. Those columns are always "Not verified".

  * Google autocomplete (public suggest endpoint): real phrases people type.
  * Google Trends via pytrends (unofficial, often rate-limited from cloud IPs):
    relative interest by calendar month. If it fails, seasonality falls back to
    the months you declared in config.yaml and is labelled "user_declared".
"""
from __future__ import annotations

import re
import string
import time
from typing import Optional

import requests

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
PREFIXES = ["best", "how to", "why", "is", "what"]


def autocomplete(term: str, timeout: int = 10) -> list:
    try:
        r = requests.get(SUGGEST_URL,
                         params={"client": "firefox", "q": term, "hl": "en", "gl": "us"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return [s for s in data[1] if isinstance(s, str)]
    except Exception:  # noqa: BLE001
        return []


def classify_intent(kw: str) -> str:
    k = kw.lower()
    if re.search(r"\b(buy|price|cheap|deal|coupon|under \$?\d+|for sale)\b", k):
        return "transactional"
    if re.search(r"\b(best|top|review|reviews|vs|versus|compare|comparison|alternative)\b", k):
        return "commercial"
    if re.search(r"\b(how|why|what|when|can|does|is|are|should)\b", k):
        return "informational"
    return "unclear"


def collect_keywords(seeds: list, alphabet: bool = True, max_total: int = 80,
                     pause: float = 0.25) -> list:
    """Return rows: {keyword, intent, source, search_volume, difficulty}."""
    seen, rows = set(), []
    seed_words = {w for sd in seeds for w in re.findall(r"[a-z0-9]+", sd.lower())}

    def good(kw):
        words = re.findall(r"[a-z0-9$]+", kw)
        if len(words) < 2 or len(words) > 10:
            return False
        if any(a == b for a, b in zip(words, words[1:])):          # "best best ..."
            return False
        if len(words[-1]) == 1 and words[-1].isalpha() and words[-1] not in ("a", "i"):
            return False                                            # alphabet-suffix echo
        if len(words[-1]) == 1:
            return False
        hits = sum(1 for w in words if w in seed_words)
        return hits >= max(1, len(seed_words) // 2)                 # stays on topic

    def add(kw):
        kw = re.sub(r"\s+", " ", kw.strip().lower())
        if kw and kw not in seen and good(kw) and len(rows) < max_total:
            seen.add(kw)
            rows.append({
                "keyword": kw,
                "intent": classify_intent(kw),
                "source": "google_autocomplete",
                "search_volume": "Not verified",
                "difficulty": "Not verified",
            })

    for seed in seeds:
        queries = [seed] + [f"{p} {seed}" for p in PREFIXES]
        if alphabet:
            queries += [f"{seed} {c}" for c in string.ascii_lowercase]
        for q in queries:
            for s in autocomplete(q):
                add(s)
            time.sleep(pause)
    # commercial / transactional first: those are the ones that sell products
    order = {"transactional": 0, "commercial": 1, "informational": 2, "unclear": 3}
    rows.sort(key=lambda r: order.get(r["intent"], 3))
    return rows


def seasonality(keyword: str, declared_peak_months: list) -> dict:
    """Relative interest by calendar month (US, 5 years). Falls back to declared months."""
    fallback = {
        "status": "user_declared",
        "peak_months": sorted(declared_peak_months or []),
        "index_by_month": {},
        "note": "Google Trends unavailable; using the months declared in config.yaml.",
    }
    try:
        from pytrends.request import TrendReq
        py = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        py.build_payload([keyword], timeframe="today 5-y", geo="US")
        df = py.interest_over_time()
        if df is None or df.empty or keyword not in df:
            return fallback
        monthly = df[keyword].groupby(df.index.month).mean()
        top = monthly.max()
        if not top:
            return fallback
        index = {int(m): round(float(v) / float(top) * 100) for m, v in monthly.items()}
        peaks = sorted(m for m, v in index.items() if v >= 70)
        return {"status": "verified", "peak_months": peaks, "index_by_month": index,
                "note": f"Google Trends, US, last 5 years, keyword '{keyword}' (relative, 100 = peak month)."}
    except Exception:  # noqa: BLE001
        return fallback


def month_in_season(season: dict, month: int) -> Optional[bool]:
    peaks = season.get("peak_months") or []
    return (month in peaks) if peaks else None
