"""
exporters.py
------------
Per article, under output/<article>/:

  optimized_article.<docx|html|md>  your article with corrected headings, keywords placed and density tuned (written by main.py)
  rank_math_fields.md               copy-paste: focus + secondary keywords, SEO title, permalink, meta description
  heading_structure.md              the H1/H2/H3 structure before and after
  topical_coverage.md               search intent, what searchers expect, which topics your article covers or misses
  changes.md                        every changed paragraph, before and after (so nothing is a surprise)
  seo_report.md                     summary, keyword usage by category, Rank Math checklist before / after
  seo_package.json                  everything, machine-readable
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import checks

ICON = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "n/a": "n/a"}


def mark(text: str, kws: list) -> str:
    spans = []
    for k in sorted({k for k in kws if k}, key=lambda k: -len(k)):
        pat = r"(?<![A-Za-z0-9])" + r"[\s-]+".join(re.escape(w) for w in k.split()) + r"(?![A-Za-z0-9])"
        for m in re.finditer(pat, text, re.I):
            if not any(m.start() < e and m.end() > s for s, e in spans):
                spans.append((m.start(), m.end()))
    out, last = [], 0
    for s, e in sorted(spans):
        out += [text[last:s], "**", text[s:e], "**"]
        last = e
    out.append(text[last:])
    return "".join(out)


def all_kws(kw: dict) -> list:
    return [kw["focus"]] + kw["secondary"] + kw["semantic"]


def rank_math_field(kw: dict) -> str:
    return ", ".join([kw["focus"]] + kw["secondary"][:4])          # Rank Math: first = focus, 5 keywords in total


def yn(v) -> str:
    return "-" if v is None else ("yes" if v else "no")


# ------------------------------------------------------------------ files
def fields_md(ctx: dict) -> str:
    kw, plan = ctx["kw"], ctx["plan"]
    L = [f"# Rank Math fields: {plan['h1']}", "",
         "Paste these into the Rank Math panel of the post, and publish the text of `optimized_article`.", "",
         "**Focus Keyword(s)** (first is the focus keyword; Rank Math allows 5 in total):", "", "```", rank_math_field(kw), "```", ""]
    if kw["semantic"]:
        L += ["**Semantic keywords** are not entered in Rank Math. They are worked into the text and headings instead: " + ", ".join(kw["semantic"]), ""]
    if plan.get("seo_title"):
        L += ["**SEO Title:**", "", "```", plan["seo_title"], "```", f"({len(plan['seo_title'])} characters)", ""]
    if plan.get("meta_description"):
        L += ["**Meta Description:**", "", "```", plan["meta_description"], "```", f"({len(plan['meta_description'])} characters)", ""]
    L += ["**Permalink (slug):**", "", "```", plan["slug"], "```",
          "If the post is already published, keep its current URL unless you add a redirect. Changing a live URL without one loses its links and rankings.", "",
          f"**H1 (article title):** {plan['h1']}", "",
          "**Rank Math preview:** check the title and description in Rank Math's snippet preview. It measures pixel width, which can differ from character counts."]
    return "\n".join(L) + "\n"


def tree(outline: list) -> list:
    return [f"{'  ' * (lvl - 1)}- H{lvl}: {text}" for lvl, text in outline]


def structure_md(ctx: dict) -> str:
    L = ["# Heading structure", "", "## Before", "", *tree(ctx["outline_before"]), "", "## After", "", *tree(ctx["outline_after"]), ""]
    if ctx["heading_changes"]:
        L += ["## What changed", "", "| Was | Now |", "|---|---|",
              *[f"| {a} | {b} |" for a, b in ctx["heading_changes"]], ""]
    for n in ctx["structure_notes"]:
        L.append(f"- {n}")
    return "\n".join(L) + "\n"


def coverage_md(ctx: dict) -> str:
    intent, cov = ctx["plan"]["intent"], ctx["plan"]["coverage"]
    L = [f"# Search intent and topical coverage: {ctx['kw']['focus']}", "",
         f"**Search intent:** {intent['primary']}  |  **Does the article serve it?** {intent['match']}", "", intent["notes"], "",
         "**What a person searching this keyword expects:**", *[f"- {w}" for w in intent["wants"]], "",
         "## Topic coverage", "",
         "The agent does not write missing sections: it cannot know facts you have not given it. Use this list to decide what to add.", "",
         "| Topic | Status | Where it is covered | Suggested heading | Questions to answer |", "|---|---|---|---|---|"]
    for c in cov:
        L.append(f"| {c['topic']} | {c['status']} | {c['where'] or '-'} | {c['suggested_heading'] or '-'} | {'; '.join(c['questions']) or '-'} |")
    if not cov:
        L.append("| (no coverage review returned) | | | | |")
    gaps = [c for c in cov if c["status"] == "missing"]
    L += ["", f"**{len(gaps)} topic(s) missing**" + (": " + ", ".join(c["topic"] for c in gaps) if gaps else "")]
    return "\n".join(L) + "\n"


def changes_md(ctx: dict) -> str:
    kws = all_kws(ctx["kw"])
    ed = ctx["edits"]
    L = ["# Changes to the text", "", "Keywords are shown in **bold** here only. Headings are in `heading_structure.md`.", ""]
    if not ed["edits"]:
        L.append("No paragraph was rewritten.")
    for e in ed["edits"]:
        L += [f"### Paragraph {e['idx']}", "", "**Before**", "", e["before"], "", "**After**", "", mark(e["after"], kws), ""]
    gr = ctx.get("grammar") or {}
    if gr.get("edits"):
        L += ["## Grammar and spelling fixes", ""]
        for e in gr["edits"]:
            L += [f"- Paragraph {e['idx']}: {e['before'][:90]}... -> {e['after'][:90]}...", ""]
    if ed["rejected"]:
        L += ["## Edits the agent refused", "", "The model proposed these but they broke a safety rule, so they were not applied:", ""]
        for r in ed["rejected"]:
            L.append(f"- Paragraph {r['idx']} (round {r['round']}): {r['reason']}")
    return "\n".join(L) + "\n"


def keyword_rows(ctx: dict) -> list:
    kw, plan, art = ctx["kw"], ctx["plan"], ctx["article_after"]
    title, meta, slug = plan.get("seo_title") or "", plan.get("meta_description") or "", plan["slug"]
    subs = [t for _l, t in art.headings]
    body = art.body_text
    rows = []
    for cat, ks in (("focus", [kw["focus"]]), ("secondary", kw["secondary"]), ("semantic", kw["semantic"])):
        for k in ks:
            rows.append({
                "category": cat, "keyword": k,
                "title": checks.has_phrase(title, k), "meta": checks.has_phrase(meta, k),
                "h1": checks.has_phrase(plan.get("h1", ""), k),
                "subheadings": sum(1 for t in subs if checks.has_phrase(t, k)),
                "body": checks.count_phrase(body, k),
                "first10": checks.has_phrase(checks.first_10_percent(art.words), k),
                "slug": (checks.slugify(k) in slug) if cat == "focus" else None,
            })
    return rows


def report_md(ctx: dict) -> str:
    cfg, plan = ctx["cfg"], ctx["plan"]
    sb, sa = checks.summarize(ctx["rows_before"]), checks.summarize(ctx["rows_after"])
    fin = ctx["final_density"]
    tgt, tol = cfg["seo"]["density_target"], cfg["seo"]["density_tolerance"]
    hist = ctx["edits"]["history"]
    L = [f"# SEO report: {plan['h1']}",
         f"*File:* `{Path(ctx['article_before'].path).name}` | *Words:* {ctx['article_before'].word_count:,} -> {ctx['article_after'].word_count:,} | *Run date:* {ctx['today']}", "",
         "> Your original file was not changed. The optimized copy is `optimized_article`. Read `changes.md` before you publish it.",
         "> Checks marked *approx* are the agent's approximation of Rank Math's tests; Rank Math's own score has the final word.", "",
         "## 1. Result",
         f"- **Keyword density:** {hist[0]['density']}% -> **{fin}%** (target {tgt}% +/- {tol}). "
         + ("On target." if abs(fin - tgt) <= tol else "Not on target: see the run notes.")
         + " Rank Math's own best band is 1-1.5% and it warns above 2.5%, so 1.7% sits just above its best band and well under its warning level.",
         f"- **Checklist:** {sb['pass']} pass / {sb['warn']} warn / {sb['fail']} fail before -> **{sa['pass']} pass / {sa['warn']} warn / {sa['fail']} fail** after, "
         f"out of {sa['total']} testable checks (images and links are not tested).",
         f"- **Search intent:** {plan['intent']['primary']} (article serves it: {plan['intent']['match']}). Topics missing: "
         f"{sum(1 for c in plan['coverage'] if c['status'] == 'missing')}. See `topical_coverage.md`.",
         f"- **Paragraphs rewritten:** {len(ctx['edits']['edits'])} (refused by the safety rules: {len(ctx['edits']['rejected'])}).", "",
         "## 2. Keywords by category", "",
         "| Category | Keyword | SEO title | Meta | H1 | Subheadings | Body mentions | First 10% | In slug |", "|---|---|---|---|---|---|---|---|---|"]
    for r in ctx["kw_rows"]:
        sug = " (suggested: confirm)" if r["keyword"] in ctx["suggested"] else ""
        L.append(f"| {r['category']} | {r['keyword']}{sug} | {yn(r['title'])} | {yn(r['meta'])} | {yn(r['h1'])} | "
                 f"{r['subheadings']} | {r['body']} | {yn(r['first10'])} | {yn(r['slug'])} |")
    L += ["", "Focus and secondary keywords count toward density. Semantic keywords are supporting vocabulary and do not.", "",
          "## 3. Density tuning", "", "| Round | Density | Keyword mentions | Paragraphs asked | Accepted |", "|---|---|---|---|---|"]
    for h in hist:
        L.append(f"| {h['round']} | {h['density']}% | {h['count']} of {h['words']} words | {h.get('asked', '-')} | {h.get('accepted', '-')} |")
    L += ["", "## 4. Rank Math checklist", "", "| Group | Test | Before | After | Detail (after) |", "|---|---|---|---|---|"]
    before = {r["test"]: r for r in ctx["rows_before"]}
    for r in ctx["rows_after"]:
        b = before.get(r["test"])
        L.append(f"| {r['group']} | {r['test']} | {ICON[b['status']] if b else '-'} | {ICON[r['status']]}{'' if r['exact'] else ' (approx)'} | {r['detail']} |")
    if ctx["notes"]:
        L += ["", "## Run notes", *[f"- {n}" for n in ctx["notes"]]]
    L += ["", "## What to do next",
          "1. Read `changes.md` and `heading_structure.md`. Fix anything you disagree with directly in `optimized_article`.",
          "2. Paste the values from `rank_math_fields.md` into Rank Math.",
          "3. Use `topical_coverage.md` to decide which missing topics to add (the agent only works with facts already in your article).",
          "4. Add images and links in WordPress (not handled here), then let Rank Math's own score confirm."]
    return "\n".join(L) + "\n"


def write_reports(out: Path, ctx: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "rank_math_fields.md").write_text(fields_md(ctx), encoding="utf-8")
    (out / "heading_structure.md").write_text(structure_md(ctx), encoding="utf-8")
    (out / "topical_coverage.md").write_text(coverage_md(ctx), encoding="utf-8")
    (out / "changes.md").write_text(changes_md(ctx), encoding="utf-8")
    (out / "seo_report.md").write_text(report_md(ctx), encoding="utf-8")
    pkg = {
        "file": Path(ctx["article_before"].path).name, "run_date": ctx["today"],
        "keywords": ctx["kw"], "seo": {k: ctx["plan"].get(k) for k in ("h1", "seo_title", "meta_description", "slug")},
        "intent": ctx["plan"]["intent"], "coverage": ctx["plan"]["coverage"],
        "outline_before": ctx["outline_before"], "outline_after": ctx["outline_after"],
        "density": {"history": ctx["edits"]["history"], "final": ctx["final_density"]},
        "edits": ctx["edits"]["edits"], "rejected_edits": ctx["edits"]["rejected"],
        "checks_before": ctx["rows_before"], "checks_after": ctx["rows_after"], "notes": ctx["notes"],
    }
    (out / "seo_package.json").write_text(json.dumps(pkg, indent=2, ensure_ascii=False), encoding="utf-8")


def write_audit_only(out: Path, article, kw: dict, rows: list, note: str, today: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    s = checks.summarize(rows)
    L = [f"# SEO audit only: {article.title or Path(article.path).name}", "",
         f"No optimized copy was made: {note}", "", f"{s['pass']} pass / {s['warn']} warn / {s['fail']} fail", "",
         "| Group | Test | Result | Detail |", "|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['group']} | {r['test']} | {ICON[r['status']]}{'' if r['exact'] else ' (approx)'} | {r['detail']} |")
    (out / "seo_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
