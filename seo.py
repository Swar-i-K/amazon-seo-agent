"""
seo.py
------
LLM steps (free Groq) wrapped in deterministic guard rails.

The model SUGGESTS: SEO title, meta description, secondary keywords, keyword map,
heading rewrites, an intro tweak, FAQs, a comparison table, internal links, image alt
text. Python then:
  * re-checks title/meta against the Rank Math rules and asks the model to fix failures,
  * builds the slug itself from the focus keyword (guaranteed to contain it),
  * drops heading rewrites that do not point at a real heading,
  * drops internal links whose URL is not in site_pages.csv or whose anchor text is not in the article,
  * replaces any number in a table cell or FAQ answer that does not appear in the article,
  * enforces length limits on social posts.
Nothing here edits your article.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

import checks
import llm

MAX_WORDS_TO_MODEL = 2500
NUM_TOKEN = re.compile(r"[$]?\d[\d,.]*")


# ------------------------------------------------------------------ helpers
def digest(article) -> tuple:
    """Return (text for the model, truncated?)."""
    words = article.words
    truncated = len(words) > MAX_WORDS_TO_MODEL
    body = " ".join(words[:MAX_WORDS_TO_MODEL])
    heads = "\n".join(f"{'#' * l} {t}" for l, t in article.headings) or "(no subheadings)"
    return (f"TITLE: {article.title or '(none)'}\nSUBHEADINGS:\n{heads}\n\nBODY TEXT"
            f"{' (first %d words)' % MAX_WORDS_TO_MODEL if truncated else ''}:\n{body}"), truncated


def _ask_json(prompt: str, cfg: dict, temperature: float = 0.3) -> dict:
    model = cfg["llm"]["model"]
    mx = cfg["llm"].get("max_tokens", 6000)
    last = None
    for _ in range(2):
        try:
            return llm.extract_json(llm.chat(prompt, model, mx, temperature))
        except llm.LLMUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"model output could not be used after retry: {last}")


def _num_norm(tok: str) -> str:
    return tok.replace(",", "").lstrip("$").rstrip(".")


def _numbers_ok(text: str, article_text: str) -> bool:
    """Every whole number in `text` must also appear as a whole number in the article ("3" does not match "36")."""
    known = {_num_norm(t) for t in NUM_TOKEN.findall(article_text)}
    return all(_num_norm(t) in known for t in NUM_TOKEN.findall(text) if _num_norm(t))


# ------------------------------------------------------------------ focus keyword (only when jobs.csv leaves it blank)
def suggest_focus_keyword(article, pool: list, cfg: dict) -> str:
    cand = "\n".join(f"- {k}" for k in pool[:30]) or "(none)"
    text, _ = digest(article)
    prompt = f"""Pick the single best Rank Math focus keyword for this existing article.
It must be a phrase a US shopper would type into Google, 2 to 6 words, lowercase, and the article must genuinely be about it.
Prefer one from the candidate list when it fits; otherwise use a close natural phrase from the article.

CANDIDATES:
{cand}

{text[:7000]}

Return JSON only: {{"focus_keyword": "<phrase>"}}"""
    kw = str(_ask_json(prompt, cfg, 0.1).get("focus_keyword", "")).strip().lower()
    kw = re.sub(r"[^a-z0-9' -]", "", kw)
    if not (2 <= len(kw.split()) <= 10):
        raise RuntimeError(f"model returned an unusable focus keyword: {kw!r}")
    return kw


# ------------------------------------------------------------------ core SEO package
def build_core_prompt(article, primary, pool, site_pages, cfg) -> str:
    text, _ = digest(article)
    year = date.today().year
    rm = cfg["rank_math"]
    pages = "\n".join(f"{p['url']} | {p['title']}" for p in site_pages[:40]) or "(none provided)"
    pool_txt = "\n".join(f"- {k}" for k in pool[:30]) or "(none provided)"
    n_img = len([i for i in article.images if i.get("media") != "video"])
    return f"""You are an on-page SEO specialist for a WordPress site that uses the Rank Math plugin.
The article below ALREADY EXISTS. Do not rewrite it. Produce SEO fields and small, targeted fixes only.

FOCUS KEYWORD (exact, must be used verbatim): "{primary}"
CURRENT YEAR: {year}
KEYWORD IDEAS (from the research step or Google autocomplete):
{pool_txt}

SITE PAGES AVAILABLE FOR INTERNAL LINKS (url | title):
{pages}

IMAGES IN THE ARTICLE: {n_img}

{text}

Return ONE JSON object and nothing else:
{{
  "seo_title": "<at most {rm['title_max_chars']} characters, MUST START with the exact focus keyword, include a number (the year {year} or a count you can verify from the headings above) and one power/positive word>",
  "meta_description": "<{rm['meta_min_chars']} to {rm['meta_max_chars']} characters, contains the exact focus keyword, ends with a reason to click, no prices or ratings>",
  "secondary_keywords": ["<up to {rm['secondary_keywords']} natural variations a reader would search, each 2-6 words>"],
  "keyword_map": [{{"keyword": "<kw>", "intent": "commercial|informational|transactional", "place": "H2|H3|intro|FAQ|image alt|meta|body", "note": "<short>"}}],
  "heading_fixes": [{{"current": "<an existing subheading, copied exactly>", "suggested": "<improved version>", "reason": "<short>"}}],
  "intro_fix": {{"current_sentence": "<the first sentence of the body text, copied exactly>", "suggested": "<same meaning, includes the exact focus keyword, at most 40 words>"}},
  "faqs": [{{"question": "<question ending with ?>", "answer": "<20-70 words, using ONLY facts stated in the article>"}}],
  "comparison_table": {{"worth_adding": true, "reason": "<short>", "caption": "<short>", "columns": ["<col>", "<col>"], "rows": [["<cell>", "<cell>"]]}},
  "internal_links": [{{"url": "<one URL from the site pages list>", "anchor_text": "<2-5 words that ALREADY appear in the article text>", "section": "<subheading where the link fits>"}}],
  "image_alts": [{{"image_number": 1, "alt": "<describes the image, at most 110 characters; only one alt may contain the focus keyword>"}}]
}}

RULES
- Use the exact focus keyword in the title start and meta description. Do not stuff it anywhere.
- heading_fixes: at most 3 items. At most 2 suggested headings may contain the exact focus keyword; the others may use a secondary keyword. Never invent a heading that is not an improvement of an existing one.
- faqs: exactly {cfg.get('faq_count', 5)} if the article supports them, fewer if it does not. If the article does not contain the answer, skip the question. Never invent specs, prices, ratings or review counts.
- comparison_table: set worth_adding to false (and rows to []) unless the article compares at least 2 products or options on shared attributes. Use only attribute values stated in the article; write "Not stated" otherwise. At most 6 columns and 8 rows.
- internal_links: use only URLs from the list above (up to 4). If the list is "(none provided)", return [].
- image_alts: return [] if there are no images.
- Do not copy long passages from the article. Plain text only, no HTML.
"""


def validate_core(raw: dict, article, site_pages: list, primary: str, cfg: dict) -> dict:
    rm = cfg["rank_math"]
    body = article.body_text
    body_n = checks.norm(body)
    body_words = set(re.findall(r"[a-z0-9']+", body_n))
    out = {"notes": []}

    # --- title / meta (strings only; length and keyword rules are enforced in the repair loop)
    out["seo_title"] = re.sub(r"\s+", " ", str(raw.get("seo_title") or "")).strip() or None
    out["meta_description"] = re.sub(r"\s+", " ", str(raw.get("meta_description") or "")).strip() or None

    # --- secondary keywords: must be on-topic (in the article) and not the primary
    sec, seen = [], {checks.norm(primary)}
    for k in raw.get("secondary_keywords") or []:
        k = re.sub(r"[^a-z0-9' -]", "", str(k).lower()).strip()
        if not (2 <= len(k.split()) <= 6) or checks.norm(k) in seen:
            continue
        if not checks.has_phrase(body, k):
            content_words = [w for w in checks.norm(k).split() if w not in checks.STOP]
            present = sum(1 for w in content_words if w in body_words)
            if not content_words or present / len(content_words) < 0.6:      # must be on-topic for THIS article
                continue
        seen.add(checks.norm(k))
        sec.append(k)
    out["secondary_keywords"] = sec[: rm.get("secondary_keywords", 3)]

    # --- keyword map
    kmap = [{"keyword": primary, "intent": "primary", "place": "title, meta, URL, intro, 1-2 subheadings",
             "note": "Focus keyword. Use the exact phrase."}]
    for r in raw.get("keyword_map") or []:
        k = str(r.get("keyword", "")).strip().lower()
        if not k or checks.norm(k) == checks.norm(primary) or len(k.split()) > 8:
            continue
        kmap.append({"keyword": k[:80],
                     "intent": r.get("intent") if r.get("intent") in ("commercial", "informational", "transactional") else "unclear",
                     "place": str(r.get("place", "body"))[:40], "note": str(r.get("note", ""))[:140]})
    out["keyword_map"] = kmap[:12]

    # --- heading fixes: must reference a real subheading
    real = {checks.norm(t): t for _l, t in article.headings}
    fixes, prim_used = [], 0
    for f in raw.get("heading_fixes") or []:
        cur = real.get(checks.norm(str(f.get("current", ""))))
        sug = re.sub(r"\s+", " ", str(f.get("suggested", ""))).strip()
        if not cur or not sug or checks.norm(sug) == checks.norm(cur) or len(sug) > 110:
            continue
        if checks.has_phrase(sug, primary):
            if prim_used >= 2:
                continue
            prim_used += 1
        fixes.append({"current": cur, "suggested": sug, "reason": str(f.get("reason", ""))[:160]})
    out["heading_fixes"] = fixes[:3]

    # --- intro fix: the quoted sentence must really be there
    intro = raw.get("intro_fix") or {}
    cs, sg = str(intro.get("current_sentence", "")).strip(), str(intro.get("suggested", "")).strip()
    out["intro_fix"] = None
    if cs and sg and checks.norm(cs) in body_n and checks.has_phrase(sg, primary) and len(sg.split()) <= 45:
        out["intro_fix"] = {"current_sentence": cs, "suggested": sg}

    # --- FAQs: numbers must come from the article
    faqs, qseen = [], set()
    for f in raw.get("faqs") or []:
        q, a = str(f.get("question", "")).strip(), re.sub(r"\s+", " ", str(f.get("answer", ""))).strip()
        if not q.endswith("?") or not a or checks.norm(q) in qseen:
            continue
        if not _numbers_ok(a, body):
            out["notes"].append(f"Dropped an FAQ because its answer contained a number not found in the article: {q}")
            continue
        qseen.add(checks.norm(q))
        faqs.append({"question": q[:200], "answer": a[:700]})
    out["faqs"] = faqs[: cfg.get("faq_count", 5)]

    # --- comparison table
    ct = raw.get("comparison_table") or {}
    out["comparison_table"] = None
    cols = [str(c).strip()[:60] for c in (ct.get("columns") or [])][:6]
    rows_in = ct.get("rows") or []
    if ct.get("worth_adding") and 2 <= len(cols) and len(rows_in) >= 2:
        rows_out = []
        for r in rows_in[:8]:
            if not isinstance(r, list) or len(r) != len(cols):
                continue
            cells = []
            for c in r:
                c = re.sub(r"\s+", " ", str(c)).strip()[:80]
                if NUM_TOKEN.search(c) and not _numbers_ok(c, body):
                    c = "Not verified"
                cells.append(c)
            rows_out.append(cells)
        if len(rows_out) >= 2:
            out["comparison_table"] = {"caption": str(ct.get("caption", ""))[:120], "columns": cols,
                                       "rows": rows_out, "reason": str(ct.get("reason", ""))[:200]}

    # --- internal links
    allowed = {p["url"]: p["title"] for p in site_pages}
    links, used = [], set()
    for l in raw.get("internal_links") or []:
        url, anchor = str(l.get("url", "")).strip(), str(l.get("anchor_text", "")).strip()
        if url not in allowed or url in used or len(anchor.split()) < 2 or not checks.has_phrase(body, anchor):
            continue
        used.add(url)
        links.append({"url": url, "page_title": allowed[url], "anchor_text": anchor,
                      "section": str(l.get("section", ""))[:120]})
    out["internal_links"] = links[:4]

    # --- image alts
    n_img = len([i for i in article.images if i.get("media") != "video"])
    alts, prim_alt = [], 0
    for a in raw.get("image_alts") or []:
        try:
            n = int(a.get("image_number"))
        except (TypeError, ValueError):
            continue
        alt = re.sub(r"\s+", " ", str(a.get("alt", ""))).strip()
        if not (1 <= n <= n_img) or not alt or len(alt) > 125:
            continue
        if checks.has_phrase(alt, primary):
            if prim_alt >= 1:
                continue
            prim_alt += 1
        alts.append({"image_number": n, "alt": alt})
    out["image_alts"] = alts
    return out


def generate_core(article, primary: str, pool: list, site_pages: list, cfg: dict) -> dict:
    core = validate_core(_ask_json(build_core_prompt(article, primary, pool, site_pages, cfg), cfg),
                         article, site_pages, primary, cfg)

    # repair loop: Rank Math rules on title and meta are checked by Python, fixed by the model
    for attempt in range(2):
        problems = checks.metadata_problems(primary, core["seo_title"], core["meta_description"], cfg)
        if not problems:
            break
        prompt = f"""Fix these problems with the SEO fields below and return JSON only: {{"seo_title": "...", "meta_description": "..."}}

PROBLEMS:
- """ + "\n- ".join(problems) + f"""

CURRENT seo_title: {core['seo_title']}
CURRENT meta_description: {core['meta_description']}
FOCUS KEYWORD (exact): "{primary}"
ARTICLE TITLE: {article.title}
Count characters carefully. Keep the same meaning. Do not add prices or ratings."""
        try:
            fixed = _ask_json(prompt, cfg, 0.2)
            t = re.sub(r"\s+", " ", str(fixed.get("seo_title") or "")).strip() or core["seo_title"]
            m = re.sub(r"\s+", " ", str(fixed.get("meta_description") or "")).strip() or core["meta_description"]
            core["seo_title"], core["meta_description"] = t, m
        except llm.LLMUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            core["notes"].append(f"Repair attempt {attempt + 1} failed: {e}")

    problems = checks.metadata_problems(primary, core["seo_title"], core["meta_description"], cfg)
    if problems and any("seo_title" in p for p in problems):
        fallback = primary.title()
        if len(fallback) <= cfg["rank_math"]["title_max_chars"]:
            core["notes"].append("The model's title could not be fixed, so the title is the focus keyword in Title Case. "
                                 "Improve it by hand.")
            core["seo_title"] = fallback
            problems = checks.metadata_problems(primary, core["seo_title"], core["meta_description"], cfg)
    core["unresolved"] = problems
    for p in problems:
        core["notes"].append(f"Unresolved after repair: {p}")

    t = core["seo_title"] or ""
    nums = [n for n in re.findall(r"\d+", t) if n != str(date.today().year)]
    if nums:
        core["notes"].append(f"The SEO title contains the number {', '.join(nums)}. Confirm it matches your article before using it.")
    core["slug"] = checks.slugify(primary)
    return core


# ------------------------------------------------------------------ social extras (optional)
def build_extras_prompt(article, primary, want_pins, want_fb) -> str:
    text, _ = digest(article)
    pins_rule = "exactly 3, each with a clearly different angle" if want_pins else "return []"
    fb_rule = ("exactly 3 posts; at most ONE may be humor, and the humor must be about the reader's own "
               "seasonal home problem, never mocking people or products") if want_fb else "return []"
    return f"""You are a social media editor for a home-comfort buying-advice brand. Use ONLY the article below.
Focus topic: "{primary}"

{text[:9000]}

Return ONE JSON object and nothing else:
{{
  "pins": [{{"angle": "<distinct angle, e.g. a problem the reader has>", "title": "<max 90 characters>", "description": "<max 450 characters, natural use of the topic, ends with a soft call to read the guide>", "image_direction": "<what a vertical 2:3 pin image shows, plus up to 8 words of text overlay>"}}],
  "facebook": [{{"style": "tip|checklist|humor", "text": "<max 450 characters>"}}]
}}

RULES
- pins: {pins_rule}.
- facebook: {fb_rule}.
- Use only facts stated in the article. No prices, star ratings, review counts, sales claims or invented statistics.
- No links, and at most 2 hashtags per Facebook post. Write "[article link]" where the link goes.
- Do not copy sentences from the article.
"""


def validate_extras(raw: dict, article, primary: str) -> dict:
    body = article.body_text
    pins, angles = [], set()
    for p in raw.get("pins") or []:
        title = re.sub(r"\s+", " ", str(p.get("title", ""))).strip()
        desc = re.sub(r"\s+", " ", str(p.get("description", ""))).strip()
        img = re.sub(r"\s+", " ", str(p.get("image_direction", ""))).strip()
        ang = checks.norm(str(p.get("angle", "")))
        if not (title and desc and img) or ang in angles or len(title) > 100 or len(desc) > 500:
            continue
        if not (_numbers_ok(title, body) and _numbers_ok(desc, body)):
            continue
        angles.add(ang)
        pins.append({"angle": str(p.get("angle", ""))[:80], "title": title, "description": desc, "image_direction": img[:240]})
    fb, humor = [], 0
    for f in raw.get("facebook") or []:
        text = re.sub(r"[ \t]+", " ", str(f.get("text", ""))).strip()
        style = f.get("style") if f.get("style") in ("tip", "checklist", "humor") else "tip"
        if not text or len(text) > 500 or re.search(r"https?://", text) or not _numbers_ok(text, body):
            continue
        if style == "humor":
            if humor >= 1:
                continue
            humor += 1
        fb.append({"style": style, "text": text})
    return {"pins": pins[:3], "facebook": fb[:3]}


def generate_extras(article, primary: str, cfg: dict) -> dict:
    ex = cfg.get("extras", {})
    want_pins, want_fb = bool(ex.get("pinterest")), bool(ex.get("facebook"))
    if not (want_pins or want_fb):
        return {"pins": [], "facebook": []}
    raw = _ask_json(build_extras_prompt(article, primary, want_pins, want_fb), cfg, 0.7)
    return validate_extras(raw, article, primary)


# ------------------------------------------------------------------ preview of the result after applying the text fixes
def apply_suggestions(article, core: dict):
    """Return a COPY of the article with the suggested heading, intro and alt-text fixes applied,
    so the checklist can show the predicted result. The real article is never touched."""
    import copy
    a = copy.deepcopy(article)
    if not core:
        return a
    for f in core.get("heading_fixes") or []:
        a.headings = [(l, f["suggested"] if checks.norm(t) == checks.norm(f["current"]) else t) for l, t in a.headings]
        a.body_text = a.body_text.replace(f["current"], f["suggested"], 1)
    intro = core.get("intro_fix")
    first_ten = checks.first_10_percent(a.words)
    if intro and not checks.has_phrase(first_ten, core.get("_primary", "")) and intro["current_sentence"] in a.body_text:
        a.body_text = a.body_text.replace(intro["current_sentence"], intro["suggested"], 1)
        a.paragraphs = [p.replace(intro["current_sentence"], intro["suggested"], 1) for p in a.paragraphs]
    imgs = [i for i in a.images if i.get("media") != "video"]
    for al in core.get("image_alts") or []:
        if 1 <= al["image_number"] <= len(imgs):
            imgs[al["image_number"] - 1]["alt"] = al["alt"]
    return a
