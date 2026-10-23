"""
checks.py
---------
Plain-Python approximation of Rank Math's free content tests. No LLM here, so
every verdict is traceable.

Thresholds taken from Rank Math's own documentation (checked October 2026):
  * focus keyword in SEO title, meta description, URL, first 10% of the content
    (the whole text if it is under 300 words), the content, and subheadings
  * content length: 600 words minimum, higher scores up to 2500 words
  * keyword density: best at 1-1.5%, warning above 2.5%; secondary keywords count
  * paragraphs over about 120 words are "long"
  * title readability: keyword near the start, a number, a power word, sentiment
  * table of contents, internal and external links, images or video

APPROXIMATIONS (each row says "approx" in the report): Rank Math measures title and
description in pixel width, uses its own power-word and sentiment lists, and may
match keyword variations differently. Always confirm in the plugin before publishing.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

POWER_WORDS = set("""
ultimate proven essential powerful amazing instant effortless easy simple effective tested honest real
guaranteed secret smart quick fast best top perfect complete definitive must-have reliable solid
surprising genuine unbeatable foolproof hassle-free ultimate worth trusted expert
""".split())
SENTIMENT_WORDS = set("""
best top great easy love perfect amazing worth proven effective stop avoid worst mistakes fail bad
hate ruin save warm cozy cheap harsh costly frustrating annoying painful solve fix beat
""".split())

STOP = {"a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "that", "with", "your", "you"}


def norm(text: str) -> str:
    t = (text or "").lower().replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    t = re.sub(r"[-\u2013\u2014]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def count_phrase(text: str, phrase: str) -> int:
    p = norm(phrase)
    if not p:
        return 0
    pat = r"(?<![a-z0-9])" + r"\s+".join(re.escape(w) for w in p.split()) + r"(?![a-z0-9])"
    return len(re.findall(pat, norm(text)))


def has_phrase(text: str, phrase: str) -> bool:
    return count_phrase(text, phrase) > 0


def slugify(text: str, max_words: int = 12) -> str:
    t = re.sub(r"['\u2019]", "", (text or "").lower())
    words = [w for w in re.split(r"[^a-z0-9]+", t) if w]
    return "-".join(words[:max_words])


def first_10_percent(words: list) -> str:
    if len(words) < 300:
        return " ".join(words)
    return " ".join(words[: max(1, len(words) // 10)])


def row(group, test, status, detail, exact=True):
    return {"group": group, "test": test, "status": status, "detail": detail, "exact": exact}


def _is_internal(href: str, domain: str) -> Optional[bool]:
    if href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    if href.startswith("/") and not href.startswith("//"):
        return True
    host = urlparse(href).netloc.lower().removeprefix("www.")
    if not host:
        return True
    d = urlparse(domain).netloc.lower().removeprefix("www.") if domain else ""
    if not d:
        return False        # domain unknown: absolute links counted as external
    return host == d


def run_checks(article, primary: str, secondary: list, seo_title: Optional[str], meta: Optional[str],
               slug: Optional[str], cfg: dict) -> list:
    rm = cfg.get("rank_math", {})
    domain = (cfg.get("site") or {}).get("domain", "")
    rows = []
    words = article.words
    wc = len(words)
    kws = [primary] + [k for k in secondary if k]

    # ---------------- Basic SEO
    G = "Basic SEO"
    if seo_title is None:
        rows.append(row(G, "Focus keyword in SEO title", "n/a", "No SEO title generated."))
    else:
        rows.append(row(G, "Focus keyword in SEO title",
                        "pass" if has_phrase(seo_title, primary) else "fail", f"Title: {seo_title}"))
    if meta is None:
        rows.append(row(G, "Focus keyword in meta description", "n/a", "No meta description generated."))
    else:
        rows.append(row(G, "Focus keyword in meta description",
                        "pass" if has_phrase(meta, primary) else "fail", f"{len(meta)} characters"))
    if slug is None:
        rows.append(row(G, "Focus keyword in URL", "n/a", "No slug generated."))
    else:
        rows.append(row(G, "Focus keyword in URL", "pass" if slugify(primary) in slug else "fail", f"/{slug}/"))
    rows.append(row(G, "Focus keyword in first 10% of content",
                    "pass" if has_phrase(first_10_percent(words), primary) else "fail",
                    "Checked the whole text (under 300 words)." if wc < 300 else f"Checked the first {max(1, wc // 10)} words."))
    n_kw = count_phrase(article.body_text, primary)
    rows.append(row(G, "Focus keyword in content", "pass" if n_kw else "fail", f"{n_kw} exact match(es)"))
    if wc >= 600:
        tier = ("100%" if wc > 2500 else "70%" if wc >= 2000 else "60%" if wc >= 1500
                else "40%" if wc >= 1000 else "20%")
        rows.append(row(G, "Content length (600+ words)", "pass",
                        f"{wc:,} words. Rank Math's length score for this band: about {tier}."))
    else:
        rows.append(row(G, "Content length (600+ words)", "fail", f"{wc:,} words. Rank Math wants at least 600."))

    # ---------------- Additional
    G = "Additional"
    sub = [t for _l, t in article.headings]
    hit = [t for t in sub if has_phrase(t, primary)]
    if not sub:
        rows.append(row(G, "Focus keyword in subheadings (H2-H6)", "fail", "The article has no subheadings."))
    else:
        rows.append(row(G, "Focus keyword in subheadings (H2-H6)", "pass" if hit else "fail",
                        f"{len(hit)} of {len(sub)} subheadings contain it"))
    if not article.images_detectable:
        rows.append(row(G, "Focus keyword in image alt text", "n/a", "Plain text files carry no images."))
    elif not article.images:
        rows.append(row(G, "Focus keyword in image alt text", "n/a", "No images found."))
    else:
        alts = [i.get("alt") or "" for i in article.images if i.get("media") != "video"]
        ok = any(has_phrase(a, primary) for a in alts)
        rows.append(row(G, "Focus keyword in image alt text", "pass" if ok else "fail",
                        f"{sum(1 for a in alts if a)} of {len(alts)} images have alt text"
                        + ("" if article.fmt != "docx" else " (set in Word; WordPress needs alt text added again after upload)")))
    total = sum(count_phrase(article.body_text, k) for k in kws)
    dens = round(100 * total / wc, 2) if wc else 0.0
    if dens > 2.5:
        st = "fail"
    elif 1.0 <= dens <= 1.5:
        st = "pass"
    else:
        st = "warn"
    rows.append(row(G, "Keyword density (primary + secondary)", st,
                    f"{dens}% ({total} matches in {wc:,} words). Best: 1-1.5%. Above 2.5% is flagged as stuffing.", exact=False))
    if not article.links_detectable:
        for t in ("Internal link", "External link"):
            rows.append(row(G, t, "n/a", "Links cannot be detected in this file (Word files from the article agent drop hyperlinks). Check in WordPress."))
    else:
        kinds = [_is_internal(h, domain) for h, _ in article.links]
        n_int, n_ext = sum(1 for k in kinds if k is True), sum(1 for k in kinds if k is False)
        note = "" if domain else " (site.domain is empty in config.yaml, so every absolute link counts as external)"
        rows.append(row(G, "Internal link", "pass" if n_int else "fail", f"{n_int} internal link(s){note}"))
        rows.append(row(G, "External link", "pass" if n_ext else "fail", f"{n_ext} external link(s){note}"))

    # ---------------- Title readability
    G = "Title Readability"
    if seo_title is None:
        rows.append(row(G, "Title tests", "n/a", "No SEO title generated."))
    else:
        t = norm(seo_title)
        tw = re.findall(r"[a-z0-9$'-]+", t)
        starts = t.startswith(norm(primary))
        rows.append(row(G, "Focus keyword at the beginning of the SEO title", "pass" if starts else "fail",
                        "Starts with the keyword." if starts else "Keyword is not at the very start.", exact=False))
        rows.append(row(G, "Number in SEO title", "pass" if re.search(r"\d", seo_title) else "warn",
                        "Contains a number." if re.search(r"\d", seo_title) else "No number (Rank Math likes one).", exact=False))
        rows.append(row(G, "Power word in SEO title", "pass" if any(w in POWER_WORDS for w in tw) else "warn",
                        "Found one." if any(w in POWER_WORDS for w in tw) else "None from the agent's word list.", exact=False))
        rows.append(row(G, "Sentiment in SEO title", "pass" if any(w in SENTIMENT_WORDS for w in tw) else "warn",
                        "Found one." if any(w in SENTIMENT_WORDS for w in tw) else "None from the agent's word list.", exact=False))
        mx = rm.get("title_max_chars", 60)
        rows.append(row(G, f"SEO title length (up to {mx} characters)", "pass" if len(seo_title) <= mx else "fail",
                        f"{len(seo_title)} characters. Rank Math measures pixels, so check its preview.", exact=False))
    if meta is not None:
        mx, mn = rm.get("meta_max_chars", 160), rm.get("meta_min_chars", 110)
        st = "fail" if len(meta) > mx else ("warn" if len(meta) < mn else "pass")
        rows.append(row("Title Readability", f"Meta description length ({mn}-{mx} characters)", st,
                        f"{len(meta)} characters. Rank Math measures pixels, so check its preview.", exact=False))

    # ---------------- Content readability
    G = "Content Readability"
    rows.append(row(G, "Table of contents", "pass" if article.has_toc else "fail",
                    "Found." if article.has_toc else "None found. Add Rank Math's Table of Contents block in WordPress."))
    long_p = [p for p in article.paragraphs if len(p.split()) > 120]
    rows.append(row(G, "Short paragraphs (120 words or fewer)", "pass" if not long_p else "fail",
                    "All paragraphs are short." if not long_p else f"{len(long_p)} paragraph(s) over 120 words.", exact=False))
    if not article.images_detectable:
        rows.append(row(G, "Images or video", "n/a", "Plain text files carry no images."))
    else:
        rows.append(row(G, "Images or video", "pass" if article.images else "fail",
                        f"{len(article.images)} found." if article.images else "None found."))
    if article.extra_h1:
        rows.append(row(G, "Only one H1 (the post title)", "warn",
                        f"{len(article.extra_h1)} extra H1 in the body. WordPress already uses the post title as H1; use H2 for sections."))
    return rows


def summarize(rows: list) -> dict:
    scored = [r for r in rows if r["status"] != "n/a"]
    return {
        "pass": sum(1 for r in scored if r["status"] == "pass"),
        "warn": sum(1 for r in scored if r["status"] == "warn"),
        "fail": sum(1 for r in scored if r["status"] == "fail"),
        "na": len(rows) - len(scored),
        "total": len(scored),
    }


def metadata_problems(primary: str, title: Optional[str], meta: Optional[str], cfg: dict) -> list:
    """Problems with the generated SEO fields that a second LLM pass should fix."""
    rm = cfg.get("rank_math", {})
    out = []
    if not title:
        out.append("seo_title is missing")
    else:
        if len(title) > rm.get("title_max_chars", 60):
            out.append(f"seo_title is {len(title)} characters; it must be at most {rm.get('title_max_chars', 60)}")
        if not norm(title).startswith(norm(primary)):
            out.append(f'seo_title must START with the exact focus keyword "{primary}"')
    if not meta:
        out.append("meta_description is missing")
    else:
        if len(meta) > rm.get("meta_max_chars", 160):
            out.append(f"meta_description is {len(meta)} characters; it must be at most {rm.get('meta_max_chars', 160)}")
        if len(meta) < rm.get("meta_min_chars", 110):
            out.append(f"meta_description is only {len(meta)} characters; aim for {rm.get('meta_min_chars', 110)}-{rm.get('meta_max_chars', 160)}")
        if not has_phrase(meta, primary):
            out.append(f'meta_description must contain the exact focus keyword "{primary}"')
    return out
