"""
edits.py
--------
Step 2 of the optimizer: place the keywords and reach your density target.

Python measures, decides WHICH paragraph gets WHICH keyword, and judges the result. The model only
rewrites the paragraphs it is told to, and each rewrite is accepted only if it:
  * keeps every number exactly as it was (no new or lost numbers, so no new prices or claims),
  * adds no new proper names (capitalised words that are not in the original or in your keywords),
  * keeps the meaning (content-word overlap with the original above `similarity_min`),
  * stays about the same length,
  * really adds (or removes) the asked keyword, and never uses one keyword more than twice in a paragraph.
Paragraphs with images or links are never rewritten. Up to `max_edit_rounds` rounds are run, then the
density actually reached is reported, whether or not it hit the target.
"""
from __future__ import annotations

import math
import re

import checks
import llm
from strategy import _ask_json, clean, cwords

EXTRA_ALLOWED = set("""this these that those with when if so but because also here there what why how one some most many every each even just
now then you your it its they their we our i my not no yes into over under about""".split())


# ------------------------------------------------------------------ measuring
def density_keywords(kw: dict) -> list:
    return [kw["focus"]] + list(kw["secondary"])


def measure(doc, kw: dict, cfg: dict) -> dict:
    text = doc.body_text()
    words = text.split()
    wc = len(words)
    dkws = density_keywords(kw)
    per = {k: checks.count_phrase(text, k) for k in dkws}
    total = sum(per.values())
    dens = round(100 * total / wc, 2) if wc else 0.0
    sem = {k: checks.count_phrase(text, k) for k in kw["semantic"]}
    return {"words": wc, "count": total, "density": dens, "per_kw": per, "semantic": sem,
            "first10": checks.has_phrase(checks.first_10_percent(words), kw["focus"])}


def target_count(wc: int, cfg: dict) -> int:
    return max(1, round(cfg["seo"]["density_target"] * wc / 100))


def within(m: dict, cfg: dict) -> bool:
    return abs(m["density"] - cfg["seo"]["density_target"]) <= cfg["seo"]["density_tolerance"]


# ------------------------------------------------------------------ tasks
def candidates(doc) -> list:
    """Paragraphs the agent may rewrite."""
    return [b for b in doc.blocks if b.kind == "p" and not b.protected and len(b.cur_text.split()) >= 18]


def _score(par_text: str, heading_text: str, keyword: str) -> float:
    kws = cwords(keyword)
    if not kws:
        return 0.0
    return len(kws & cwords(par_text)) / len(kws) + 0.5 * len(kws & cwords(heading_text)) / len(kws)


def _heading_above(doc, block) -> str:
    h = ""
    for b in doc.blocks:
        if b is block:
            break
        if b.kind == "h":
            h = b.cur_text
    return h


def build_tasks(doc, kw: dict, cfg: dict, m: dict) -> tuple:
    """Return (tasks, notes). task = {"idx", "type": add|remove, "kw"}"""
    notes, tasks = [], []
    cands = candidates(doc)
    if not cands:
        return [], ["No paragraph can be edited (all are protected or too short)."]
    load = {}                                                     # idx -> number of tasks already placed there

    def place(keyword, ttype, exclude_idx=(), prefer=None):
        pool = [b for b in cands if load.get(b.idx, 0) < 2 and b.idx not in exclude_idx]
        if ttype == "add":
            pool = [b for b in pool if checks.count_phrase(b.cur_text, keyword) == 0]
            if prefer is not None:
                return prefer if prefer in pool else None
            ranked = sorted(pool, key=lambda b: (-_score(b.cur_text, _heading_above(doc, b), keyword), b.idx))
            ranked = [b for b in ranked if _score(b.cur_text, _heading_above(doc, b), keyword) > 0] or []
            if not ranked:
                return None
            # spread the additions through the article: among the best-fitting, pick the one least crowded
            best = ranked[0]
            for b in ranked[:3]:
                if load.get(b.idx, 0) < load.get(best.idx, 0):
                    best = b
            return best
        pool = [b for b in pool if checks.count_phrase(b.cur_text, keyword) >= 1]
        pool.sort(key=lambda b: (-checks.count_phrase(b.cur_text, keyword), b.idx))
        return pool[0] if pool else None

    def add_task(keyword, ttype, **kws):
        b = place(keyword, ttype, **kws)
        if b is None:
            return False
        tasks.append({"idx": b.idx, "type": ttype, "kw": keyword})
        load[b.idx] = load.get(b.idx, 0) + 1
        return True

    counted = dict(m["per_kw"])
    # 1. the focus keyword must be in the first 10% of the content
    if not m["first10"]:
        first = next((b for b in doc.blocks if b.kind == "p" and not b.protected), None)
        if first is not None and first in cands:
            add_task(kw["focus"], "add", prefer=first)
            counted[kw["focus"]] += 1
        else:
            notes.append("The first body paragraph cannot be edited (protected or very short), so the focus keyword cannot be added to the first 10%.")
    # 2. every secondary keyword at least once
    for k in kw["secondary"]:
        if counted[k] == 0 and add_task(k, "add"):
            counted[k] += 1
    # 3. every semantic keyword at least once (they do not count toward density)
    for k, n in m["semantic"].items():
        if n == 0 and not add_task(k, "add"):
            notes.append(f"The semantic keyword '{k}' matches no paragraph of the article, so it was not forced in. Add content about it or drop it.")
    # 4. density
    tc = target_count(m["words"], cfg)
    cur = sum(counted.values())
    tol = cfg["seo"]["density_tolerance"]
    if m["density"] < cfg["seo"]["density_target"] - tol:
        need = tc - cur
        order = [kw["focus"]] + list(kw["secondary"])
        cap = max(2, math.ceil(tc / 2))
        guard = 0
        while need > 0 and guard < 60:
            guard += 1
            progressed = False
            for k in order:
                if need <= 0:
                    break
                if counted[k] >= cap and k == kw["focus"]:
                    continue
                if add_task(k, "add"):
                    counted[k] += 1
                    need -= 1
                    progressed = True
            if not progressed:
                notes.append("Not enough suitable paragraphs to place more keyword mentions without forcing them.")
                break
    elif m["density"] > cfg["seo"]["density_target"] + tol:
        excess = cur - tc
        order = sorted(counted, key=lambda k: -counted[k])
        guard = 0
        while excess > 0 and guard < 60:
            guard += 1
            progressed = False
            for k in order:
                if excess <= 0:
                    break
                if counted[k] > 1 and add_task(k, "remove"):
                    counted[k] -= 1
                    excess -= 1
                    progressed = True
            if not progressed:
                break
    return tasks, notes


# ------------------------------------------------------------------ prompt + validation
def build_prompt(doc, tasks: list, kw: dict, cfg: dict) -> str:
    by_idx = {b.idx: b for b in doc.blocks}
    per_par = {}
    for t in tasks:
        per_par.setdefault(t["idx"], []).append(t)
    lines = []
    for idx, ts in per_par.items():
        b = by_idx[idx]
        what = []
        for t in ts:
            verb = ("include the exact phrase" if t["type"] == "add" else "use the exact phrase one time LESS (rephrase one mention with a pronoun or different words)")
            what.append(f'{verb} "{t["kw"]}"')
        lines.append(f"PARAGRAPH [{idx}] (section: {_heading_above(doc, b) or 'intro'})\nTASKS: " + "; ".join(what) + f"\nTEXT: {b.cur_text}")
    return f"""You are an SEO copy editor. Make the SMALLEST possible edits to the paragraphs below so each task is done naturally.

Article title: {doc.title_block().cur_text if doc.title_block() else ''}
Keywords (exact wording): focus "{kw['focus']}"; others {kw['secondary'] + kw['semantic']}

{chr(10).join(x + chr(10) for x in lines)}

Return ONE JSON object and nothing else:
{{"edits": [{{"idx": <paragraph idx>, "text": "<the full revised paragraph>"}}]}}

RULES
- Change as few words as possible. Keep the meaning, the order of ideas, the tone and every fact.
- An added keyword must appear with its EXACT wording, fit the sentence grammatically, and read naturally. Never use the same keyword more than twice in a paragraph.
- Do NOT add or change numbers, prices, ratings, brand or product names, claims, or new facts. Do not add new sentences that say something new.
- Plain text only: no markdown, no quotation marks around keywords, no line breaks.
- Return only paragraphs you changed.
"""


def _caps(text: str) -> set:
    return set(re.findall(r"\b[A-Z][A-Za-z0-9]+\b", text))


def _nums(text: str) -> set:
    return {t.replace(",", "").lstrip("$").rstrip(".") for t in re.findall(r"\$?\d[\d,]*(?:\.\d+)?", text)}


def jaccard(a: str, b: str) -> float:
    x, y = cwords(a), cwords(b)
    return len(x & y) / len(x | y) if (x | y) else 1.0


def validate_edit(orig: str, new: str, tasks: list, kw: dict, cfg: dict, sim_min: float = None) -> tuple:
    """Return (ok, reason). Every guard is deterministic."""
    sim_min = sim_min if sim_min is not None else cfg["seo"]["similarity_min"]
    if not new or new == orig:
        return False, "no change"
    if _nums(new) != _nums(orig):
        return False, "changed a number"
    allowed = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", orig)} | {w.lower() for k in checks_all(kw) for w in k.split()} | EXTRA_ALLOWED
    new_caps = {c for c in _caps(new) if c.lower() not in allowed}
    if new_caps:
        return False, f"added new names or words: {', '.join(sorted(new_caps))}"
    if jaccard(orig, new) < sim_min:
        return False, "changed the meaning too much"
    lo, ln = len(orig.split()), len(new.split())
    if abs(ln - lo) > max(14, 0.4 * lo):
        return False, "changed the length too much"
    for t in tasks:
        before, after = checks.count_phrase(orig, t["kw"]), checks.count_phrase(new, t["kw"])
        if t["type"] == "add" and after <= before:
            return False, f"did not add '{t['kw']}'"
        if t["type"] == "remove" and after >= before:
            return False, f"did not reduce '{t['kw']}'"
    for k in checks_all(kw):
        if checks.count_phrase(new, k) > 2:
            return False, f"uses '{k}' more than twice"
    return True, ""


def checks_all(kw: dict) -> list:
    return [kw["focus"]] + list(kw["secondary"]) + list(kw["semantic"])


# ------------------------------------------------------------------ the loop
def optimize_body(doc, kw: dict, cfg: dict) -> dict:
    rep = {"history": [], "edits": [], "rejected": [], "notes": []}
    by_idx = {b.idx: b for b in doc.blocks}
    originals = {}
    m = measure(doc, kw, cfg)
    rep["history"].append({"round": 0, "density": m["density"], "count": m["count"], "words": m["words"]})
    for rnd in range(1, cfg["seo"]["max_edit_rounds"] + 1):
        tasks, notes = build_tasks(doc, kw, cfg, m)
        for n in notes:
            if n not in rep["notes"]:
                rep["notes"].append(n)
        if not tasks:
            break
        raw = _ask_json(build_prompt(doc, tasks, kw, cfg), cfg, 0.3)
        per_par = {}
        for t in tasks:
            per_par.setdefault(t["idx"], []).append(t)
        accepted = 0
        for e in raw.get("edits") or []:
            try:
                idx = int(e.get("idx"))
            except (TypeError, ValueError):
                continue
            if idx not in per_par:
                continue
            b = by_idx[idx]
            new = clean(e.get("text"))
            ok, why = validate_edit(b.cur_text, new, per_par[idx], kw, cfg)
            if not ok:
                rep["rejected"].append({"idx": idx, "round": rnd, "reason": why, "tasks": per_par[idx], "text": new})
                continue
            originals.setdefault(idx, b.text)
            b.new_text = new
            accepted += 1
        m = measure(doc, kw, cfg)
        rep["history"].append({"round": rnd, "density": m["density"], "count": m["count"], "words": m["words"],
                               "asked": len(per_par), "accepted": accepted})
        if within(m, cfg) and m["first10"] and all(n > 0 for n in m["semantic"].values()) and all(n > 0 for n in m["per_kw"].values()):
            break
        if accepted == 0 and rnd >= 2:
            break
    for idx, before in originals.items():
        rep["edits"].append({"idx": idx, "before": before, "after": by_idx[idx].new_text})
    rep["final"] = m
    rep["on_target"] = within(m, cfg)
    return rep


# ------------------------------------------------------------------ optional grammar pass
def grammar_pass(doc, kw: dict, cfg: dict) -> dict:
    """Spelling, grammar and punctuation only. Keyword counts must not change."""
    out = {"edits": [], "rejected": 0}
    pars = [b for b in doc.blocks if b.kind == "p" and not b.protected and len(b.cur_text.split()) >= 12]
    for start in range(0, len(pars), 12):
        chunk = pars[start:start + 12]
        listing = "\n".join(f"[{b.idx}] {b.cur_text}" for b in chunk)
        prompt = ("Fix spelling, grammar and punctuation ONLY in the paragraphs below. Do not rewrite, reorder, add or remove ideas, "
                  "change numbers or names, or change any of these keyword phrases: " + ", ".join(checks_all(kw)) +
                  '.\nReturn JSON only: {"edits": [{"idx": <idx>, "text": "<corrected paragraph>"}]}. Return only paragraphs that needed a fix.\n\n' + listing)
        raw = _ask_json(prompt, cfg, 0.1)
        by_idx = {b.idx: b for b in chunk}
        for e in raw.get("edits") or []:
            try:
                idx = int(e.get("idx"))
            except (TypeError, ValueError):
                continue
            b = by_idx.get(idx)
            new = clean(e.get("text"))
            if b is None or not new:
                continue
            same_kw = all(checks.count_phrase(new, k) == checks.count_phrase(b.cur_text, k) for k in checks_all(kw))
            ok, why = validate_edit(b.cur_text, new, [], kw, cfg, sim_min=0.85)
            if ok and same_kw:
                out["edits"].append({"idx": idx, "before": b.cur_text, "after": new})
                b.new_text = new
            else:
                out["rejected"] += 1
    return out
