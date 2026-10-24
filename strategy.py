"""
strategy.py
-----------
Step 1 of the optimizer. The free Groq model proposes:
  search intent, a new H1, the SEO title and meta description, a corrected heading structure,
  headings that organise content the article already has, and a topical-coverage review.
Python then decides what is allowed:
  * headings must point at real headings; numbers in them must exist in the article; at most 2 contain the focus keyword exactly,
  * a heading inserted above a paragraph must describe the paragraphs that follow it (word overlap), never new content,
  * levels are normalised: one H1, sections start at H2, no skipped levels,
  * SEO title / meta / H1 are checked against Rank Math's rules and sent back for repair, then falled back to a safe version,
  * the slug is built from the focus keyword by Python.
The model never adds facts to your article and never decides what is saved.
"""
from __future__ import annotations

import re
from datetime import date

import checks
import llm

MAX_WORDS_TO_MODEL = 2600
INTENTS = ("informational", "commercial", "transactional", "navigational")
NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")
PLURAL_EXC = ("ss", "us", "is")


def _num_norm(t: str) -> str:
    return t.replace(",", "").lstrip("$").rstrip(".")


def numbers_ok(text: str, allowed: str, allow_year: bool = True) -> bool:
    known = {_num_norm(t) for t in NUM.findall(allowed)}
    for t in NUM.findall(text):
        n = _num_norm(t)
        if not n or n in known:
            continue
        if allow_year and re.fullmatch(r"20\d\d", n):
            continue
        return False
    return True


def cwords(text: str) -> set:
    out = set()
    for w in re.findall(r"[a-z0-9']+", (text or "").lower()):
        w = w.strip("'")
        if len(w) > 3 and w.endswith("s") and not w.endswith(PLURAL_EXC):
            w = w[:-1]
        if len(w) > 2 and w not in checks.STOP:
            out.add(w)
    return out


def clean(t) -> str:
    t = re.sub(r"[*_`#~>|]+", "", str(t or ""))
    return re.sub(r"\s+", " ", t).strip()


# ------------------------------------------------------------------ prompt
def blocks_text(doc, max_words: int = MAX_WORDS_TO_MODEL) -> tuple:
    lines, used, truncated = [], 0, False
    for b in doc.blocks:
        if b.kind == "table":
            tag = "TABLE"
        elif b.kind == "title":
            tag = "TITLE"
        elif b.kind == "h":
            tag = f"H{b.level}"
        elif b.kind == "li":
            tag = "LI"
        else:
            tag = "P"
        n = len(b.text.split())
        if used + n > max_words:
            truncated = True
            break
        used += n
        lines.append(f"[{b.idx}] {tag}{' (protected)' if b.protected and b.kind in ('p', 'li') else ''}: {b.text}")
    return "\n".join(lines), truncated


def build_prompt(doc, kw: dict, cfg: dict) -> str:
    rm, seo = cfg["rank_math"], cfg["seo"]
    year = date.today().year
    body, truncated = blocks_text(doc)
    power = kw.get("power_word") or "(choose one that fits, e.g. Ultimate, Proven, Essential, Simple, Honest)"
    sec = kw["secondary"] or ["(none given: propose up to %d from the article, 2-5 words each)" % rm["secondary_keywords"]]
    sem = kw["semantic"] or ["(none given: propose up to 4 related phrases that appear or are clearly implied in the article)"]
    return f"""You are an on-page SEO editor for a WordPress site that uses the Rank Math plugin.
The article below ALREADY EXISTS and its facts are correct. You restructure and optimise it. You never add facts, products, numbers or claims.

KEYWORDS (use the exact wording)
  focus keyword: "{kw['focus']}"
  primary/secondary keywords: {sec}
  semantic (related) keywords: {sem}
  power word for the SEO title: {power}
CURRENT YEAR: {year}

ARTICLE BLOCKS, in order. [idx] KIND: text   (TITLE = the H1; H2-H6 = subheadings; P = paragraph; protected = contains an image or link)
{body}
{'(The article is longer than shown; only the first %d words were given.)' % MAX_WORDS_TO_MODEL if truncated else ''}

Return ONE JSON object and nothing else:
{{
  "search_intent": {{"primary": "informational|commercial|transactional|navigational", "what_searcher_wants": ["<3-5 things a person searching the focus keyword expects to find>"], "intent_match": "good|partial|poor", "notes": "<one or two sentences on how well the article serves that intent>"}},
  "h1": "<the article's H1: contains the exact focus keyword, reads naturally, at most 80 characters>",
  "seo_title": "<at most {rm['title_max_chars']} characters, STARTS with the exact focus keyword, contains the power word and a number (the year {year} or a count that matches the article)>",
  "meta_description": "<{rm['meta_min_chars']} to {rm['meta_max_chars']} characters, contains the exact focus keyword, gives a reason to click, no prices or ratings>",
  "headings": [{{"idx": <idx of an existing heading block>, "level": 2, "text": "<improved heading>"}}],
  "new_headings": [{{"before_idx": <idx of a P block>, "level": 3, "text": "<heading describing the paragraphs that follow>"}}],
  "coverage": [{{"topic": "<subtopic a searcher expects>", "status": "covered|partial|missing", "where_idx": <heading idx or null>, "suggested_heading": "<heading for it>", "questions": ["<question the section should answer>"]}}],
  "secondary_keywords": ["<only if none were given>"],
  "semantic_keywords": ["<only if none were given>"]
}}

RULES
- Headings: logical hierarchy. H2 for the main sections, H3 for sub-points, never skip a level, no heading above H4. The article currently may have a wrong hierarchy, an extra H1, or no subheadings: fix that.
- The exact focus keyword may appear in at most 2 subheadings. Put each primary/secondary keyword in one heading (H2 or H3) only where it truly fits what that section says. Use semantic keywords in H3s or body, only where natural. Never stuff.
- headings: include every existing subheading that needs improvement (keep the ones that are already good out of the list). A heading must describe what its section really says.
- new_headings: at most 6, ONLY to organise content that is already in the article (the heading must describe the paragraphs right after it). Do not invent topics.
- coverage: 6 to 10 topics. This is a review only. Do NOT write the missing content.
- Never add numbers, prices, ratings, product names or claims that are not in the article. Plain text only, no markdown.
"""


# ------------------------------------------------------------------ validation
def _heading_text_ok(text: str, article_text: str) -> bool:
    return 3 <= len(text.split()) <= 18 and len(text) <= 110 and numbers_ok(text, article_text)


def validate_strategy(raw: dict, doc, kw: dict, cfg: dict) -> dict:
    rm = cfg["rank_math"]
    article_text = " ".join(b.text for b in doc.blocks)
    focus = kw["focus"]
    plan = {"notes": []}

    # --- intent
    si = raw.get("search_intent") or {}
    primary = str(si.get("primary", "")).strip().lower()
    plan["intent"] = {
        "primary": primary if primary in INTENTS else "unclear",
        "wants": [clean(x)[:160] for x in (si.get("what_searcher_wants") or [])][:5],
        "match": si.get("intent_match") if si.get("intent_match") in ("good", "partial", "poor") else "partial",
        "notes": clean(si.get("notes"))[:300],
    }

    plan["h1"] = clean(raw.get("h1"))
    plan["seo_title"] = clean(raw.get("seo_title")) or None
    plan["meta_description"] = clean(raw.get("meta_description")) or None

    # --- existing headings
    by_idx = {b.idx: b for b in doc.blocks}
    heads, seen, focus_uses = [], set(), 0
    for h in raw.get("headings") or []:
        try:
            idx = int(h.get("idx"))
        except (TypeError, ValueError):
            continue
        b = by_idx.get(idx)
        if b is None or b.kind != "h" or b.protected or idx in seen:
            continue
        text = clean(h.get("text"))
        try:
            lvl = int(h.get("level"))
        except (TypeError, ValueError):
            lvl = b.level
        lvl = min(4, max(2, lvl))
        if not text or not _heading_text_ok(text, article_text):
            continue
        if checks.has_phrase(text, focus):
            if focus_uses >= 2:
                continue
            focus_uses += 1
        seen.add(idx)
        heads.append({"idx": idx, "level": lvl, "text": text})
    plan["headings"] = heads

    # --- inserted headings: must describe the paragraphs right after them
    news, used = [], set()
    blocks = doc.blocks
    pos = {b.idx: i for i, b in enumerate(blocks)}
    for h in raw.get("new_headings") or []:
        try:
            idx = int(h.get("before_idx"))
        except (TypeError, ValueError):
            continue
        b = by_idx.get(idx)
        if b is None or b.kind not in ("p", "li") or idx in used:
            continue
        i = pos[idx]
        if i > 0 and blocks[i - 1].kind in ("h", "title"):
            continue                                                    # already sits under a heading
        text = clean(h.get("text"))
        try:
            lvl = int(h.get("level"))
        except (TypeError, ValueError):
            lvl = 3
        lvl = min(4, max(2, lvl))
        if not text or not _heading_text_ok(text, article_text):
            continue
        after = " ".join(x.text for x in blocks[i:i + 3] if x.kind in ("p", "li"))
        hw = cwords(text)
        if not hw or len(hw & cwords(after)) / len(hw) < 0.5:
            plan["notes"].append(f"Dropped a suggested heading that does not describe the text after it: {text}")
            continue
        if checks.has_phrase(text, focus):
            if focus_uses >= 2:
                continue
            focus_uses += 1
        used.add(idx)
        news.append({"before_idx": idx, "level": lvl, "text": text})
    plan["new_headings"] = news[:6]

    # --- coverage review (never written into the article)
    cov, heads_idx = [], {b.idx for b in doc.blocks if b.kind == "h"}
    for c in raw.get("coverage") or []:
        topic = clean(c.get("topic"))[:100]
        if not topic:
            continue
        status = c.get("status") if c.get("status") in ("covered", "partial", "missing") else "partial"
        try:
            w = int(c.get("where_idx"))
        except (TypeError, ValueError):
            w = None
        where = by_idx[w].text if (w in heads_idx) else None
        if status != "missing" and where is None:
            status = "partial" if status == "covered" else status
        cov.append({"topic": topic, "status": status, "where": where,
                    "suggested_heading": clean(c.get("suggested_heading"))[:110],
                    "questions": [clean(q)[:140] for q in (c.get("questions") or [])][:3]})
    plan["coverage"] = cov[:12]

    # --- keywords the creator did not supply
    body_words = cwords(article_text)

    def grounded(k, lo=2, hi=6):
        k = re.sub(r"[^a-z0-9' -]", "", str(k).lower()).strip()
        kws = [w for w in cwords(k)]
        if not (lo <= len(k.split()) <= hi) or not kws:
            return None
        return k if len(set(kws) & body_words) / len(kws) >= 0.6 else None
    taken = {checks.norm(focus), *[checks.norm(x) for x in kw["secondary"] + kw["semantic"]]}
    plan["suggested_secondary"], plan["suggested_semantic"] = [], []
    if not kw["secondary"]:
        for k in raw.get("secondary_keywords") or []:
            g = grounded(k)
            if g and checks.norm(g) not in taken:
                taken.add(checks.norm(g))
                plan["suggested_secondary"].append(g)
        plan["suggested_secondary"] = plan["suggested_secondary"][: rm["secondary_keywords"]]
    if not kw["semantic"]:
        for k in raw.get("semantic_keywords") or []:
            g = grounded(k, 1, 6)
            if g and checks.norm(g) not in taken:
                taken.add(checks.norm(g))
                plan["suggested_semantic"].append(g)
        plan["suggested_semantic"] = plan["suggested_semantic"][:4]
    return plan


# ------------------------------------------------------------------ title / meta / H1 rules
def seo_problems(plan: dict, kw: dict, cfg: dict, article_text: str) -> list:
    rm, focus = cfg["rank_math"], kw["focus"]
    probs = []
    h1, title, meta = plan.get("h1") or "", plan.get("seo_title") or "", plan.get("meta_description") or ""
    if not h1:
        probs.append("h1 is missing")
    else:
        if not checks.has_phrase(h1, focus):
            probs.append(f'h1 must contain the exact focus keyword "{focus}"')
        if len(h1) > 90:
            probs.append(f"h1 is {len(h1)} characters; keep it at most 80")
        if not numbers_ok(h1, article_text):
            probs.append("h1 contains a number that is not in the article")
    if not title:
        probs.append("seo_title is missing")
    else:
        if len(title) > rm["title_max_chars"]:
            probs.append(f"seo_title is {len(title)} characters; it must be at most {rm['title_max_chars']}")
        if not checks.norm(title).startswith(checks.norm(focus)):
            probs.append(f'seo_title must START with the exact focus keyword "{focus}"')
        pw = kw.get("power_word")
        words = set(re.findall(r"[a-z0-9'-]+", title.lower()))
        if pw and pw.lower() not in words:
            probs.append(f'seo_title must contain the power word "{pw}"')
        if not pw and not (words & checks.POWER_WORDS):
            probs.append("seo_title needs a power word (for example Ultimate, Proven, Essential, Simple, Honest)")
        if not re.search(r"\d", title):
            probs.append("seo_title needs a number (the current year or a count that matches the article)")
        elif not numbers_ok(title, article_text):
            probs.append("seo_title contains a number that is not in the article")
    if not meta:
        probs.append("meta_description is missing")
    else:
        if len(meta) > rm["meta_max_chars"]:
            probs.append(f"meta_description is {len(meta)} characters; it must be at most {rm['meta_max_chars']}")
        if len(meta) < rm["meta_min_chars"]:
            probs.append(f"meta_description is {len(meta)} characters; aim for {rm['meta_min_chars']}-{rm['meta_max_chars']}")
        if not checks.has_phrase(meta, focus):
            probs.append(f'meta_description must contain the exact focus keyword "{focus}"')
        if not numbers_ok(meta, article_text):
            probs.append("meta_description contains a number that is not in the article")
    return probs


def _ask_json(prompt: str, cfg: dict, temperature: float = 0.3) -> dict:
    last = None
    for _ in range(2):
        try:
            return llm.extract_json(llm.chat(prompt, cfg["llm"]["model"], cfg["llm"].get("max_tokens", 6000), temperature))
        except llm.LLMUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"model output could not be used after retry: {last}")


def generate_strategy(doc, kw: dict, cfg: dict) -> dict:
    article_text = " ".join(b.text for b in doc.blocks)
    plan = validate_strategy(_ask_json(build_prompt(doc, kw, cfg), cfg), doc, kw, cfg)

    for attempt in range(2):
        probs = seo_problems(plan, kw, cfg, article_text)
        if not probs:
            break
        prompt = ("Fix these problems with the SEO fields below and return JSON only: "
                  '{"h1": "...", "seo_title": "...", "meta_description": "..."}\n\nPROBLEMS:\n- ' + "\n- ".join(probs) +
                  f"\n\nCURRENT h1: {plan.get('h1')}\nCURRENT seo_title: {plan.get('seo_title')}\nCURRENT meta_description: {plan.get('meta_description')}"
                  f"\nFOCUS KEYWORD (exact): \"{kw['focus']}\"\nPOWER WORD: {kw.get('power_word') or '(any)'}\nCURRENT YEAR: {date.today().year}"
                  "\nCount characters carefully. Keep the same meaning. Do not add prices, ratings or numbers that are not in the article.")
        try:
            fixed = _ask_json(prompt, cfg, 0.2)
            for k in ("h1", "seo_title", "meta_description"):
                v = clean(fixed.get(k))
                if v:
                    plan[k] = v
        except llm.LLMUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            plan["notes"].append(f"Repair attempt {attempt + 1} failed: {e}")

    probs = seo_problems(plan, kw, cfg, article_text)
    focus = kw["focus"]
    if any(p.startswith("h1") for p in probs):
        orig = (doc.title_block().text if doc.title_block() else "").strip()
        cand = f"{focus.title()}: {orig}" if orig and len(focus.title()) + len(orig) + 2 <= 80 else focus.title()
        plan["h1"] = cand
        plan["notes"].append("The model's H1 could not be fixed, so the H1 is built from the focus keyword and your old title. Improve it by hand.")
    if any(p.startswith("seo_title") and ("START" in p or "characters" in p) for p in probs):
        plan["seo_title"] = focus.title()[: cfg["rank_math"]["title_max_chars"]]
        plan["notes"].append("The model's SEO title could not be fixed, so the title is the focus keyword in Title Case. Improve it by hand.")
    plan["unresolved"] = seo_problems(plan, kw, cfg, article_text)
    for p in plan["unresolved"]:
        plan["notes"].append(f"Still open after repair: {p}")
    nums = [n for n in re.findall(r"\d+", plan.get("seo_title") or "") if n != str(date.today().year)]
    if nums:
        plan["notes"].append(f"The SEO title contains the number {', '.join(nums)}. Confirm it matches your article.")
    plan["slug"] = checks.slugify(focus)
    return plan


# ------------------------------------------------------------------ apply to the document
def ensure_title(doc, text: str) -> None:
    if doc.title_block() is None and doc.blocks:
        doc.blocks[0].insert_before.insert(0, (1, text))


def apply_strategy(doc, plan: dict) -> list:
    """Write the new H1 and headings into the editable document. Returns notes."""
    notes = []
    t = doc.title_block()
    if plan.get("h1"):
        if t is None:
            ensure_title(doc, plan["h1"])
            notes.append("The article had no H1 (title) block, so one was added.")
        elif t.protected:
            notes.append("The H1 contains a link, so it was left as it is.")
        else:
            t.new_text = plan["h1"]
    by_idx = {b.idx: b for b in doc.blocks}
    for h in plan.get("headings", []):
        b = by_idx[h["idx"]]
        b.new_text, b.new_level = h["text"], h["level"]
    for h in plan.get("new_headings", []):
        by_idx[h["before_idx"]].insert_before.append((h["level"], h["text"]))
    notes += normalize_levels(doc)
    return notes


def normalize_levels(doc) -> list:
    """One H1; sections start at H2; no skipped levels (an H4 right after an H2 becomes an H3)."""
    notes, prev = [], 1
    for b in doc.blocks:
        fixed_ins = []
        for lvl, text in b.insert_before:
            if lvl == 1:
                fixed_ins.append((lvl, text))
                prev = 1
                continue
            new = min(max(lvl, 2), prev + 1)
            if new != lvl:
                notes.append(f"Inserted heading '{text}' moved from H{lvl} to H{new} (no skipped levels).")
            fixed_ins.append((new, text))
            prev = new
        b.insert_before = fixed_ins
        if b.kind == "title":
            prev = 1
        elif b.kind == "h":
            lvl = b.cur_level
            new = min(max(lvl, 2), prev + 1)
            if new != lvl:
                if b.level == 1:
                    notes.append(f"Extra H1 '{b.cur_text}' became an H{new}.")
                else:
                    notes.append(f"Heading '{b.cur_text}' moved from H{lvl} to H{new} (no skipped levels).")
            if new != b.level:
                b.new_level = new
            elif b.new_level is not None:
                b.new_level = None if new == b.level else new
            prev = new
    return notes
