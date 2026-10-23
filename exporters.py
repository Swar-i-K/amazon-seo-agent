"""
exporters.py
------------
Per-article output under output/<slug>/:

  rank_math_fields.md   copy-paste block for the Rank Math panel (focus keywords, title, permalink, description)
  seo_report.md         the full report: Rank Math checklist, fixes, keyword map, links, FAQs, table, social
  faq.html              FAQ text for the Rank Math FAQ block (or a plain HTML fallback)
  comparison_table.html table for a Custom HTML block (only when the article supports one)
  internal_links.csv    link suggestions
  social.md             Pinterest + Facebook concepts (only if enabled)
  seo_package.json      everything, machine-readable
"""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

import checks

ICON = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "n/a": "n/a"}


def focus_keyword_field(primary: str, secondary: list) -> str:
    return ", ".join([primary] + list(secondary))      # Rank Math: first = primary, up to 5 total


def rank_math_fields_md(art, primary, core, cfg) -> str:
    sec = core.get("secondary_keywords", []) if core else []
    lines = [f"# Rank Math fields: {art.title or Path(art.path).name}", "",
             "Paste these into the Rank Math panel of the existing post.", "",
             "**Focus Keyword(s)** (first is primary):", "", "```", focus_keyword_field(primary, sec), "```", ""]
    if core and core.get("seo_title"):
        lines += ["**SEO Title:**", "", "```", core["seo_title"], "```", f"({len(core['seo_title'])} characters)", ""]
    if core and core.get("meta_description"):
        lines += ["**Meta Description:**", "", "```", core["meta_description"], "```",
                  f"({len(core['meta_description'])} characters)", ""]
    if core and core.get("slug"):
        lines += ["**Permalink (slug):**", "", "```", core["slug"], "```",
                  "If this post is already published, keep its current URL unless you also add a redirect. "
                  "Changing a live URL without a redirect loses its links and rankings.", ""]
    lines += ["**Rank Math preview:** check the title and description in Rank Math's snippet preview. "
              "It measures pixel width, which can differ from character counts."]
    return "\n".join(lines) + "\n"


def faq_html(faqs: list) -> str:
    out = ["<!-- Use Rank Math's FAQ block in the editor and paste each question/answer into it.",
           "     Rank Math then adds the FAQ schema itself. Do NOT also paste JSON-LD: duplicate schema. -->"]
    for f in faqs:
        out.append(f"<h3>{html.escape(f['question'])}</h3>\n<p>{html.escape(f['answer'])}</p>")
    return "\n".join(out) + "\n"


def table_html(ct: dict) -> str:
    th = "".join(f"<th>{html.escape(c)}</th>" for c in ct["columns"])
    body = "\n".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in ct["rows"])
    cap = f"<caption>{html.escape(ct['caption'])}</caption>\n" if ct.get("caption") else ""
    return f'<table>\n{cap}<thead><tr>{th}</tr></thead>\n<tbody>\n{body}\n</tbody>\n</table>\n'


def social_md(extras: dict, primary: str) -> str:
    out = [f"# Social concepts: {primary}", "",
           "Drafts only. Add your own link and image. Do not put affiliate links directly in posts unless the platform's "
           "rules and the FTC disclosure rules are met; linking to your article (which carries the disclosure) is safer.", ""]
    if extras.get("pins"):
        out += ["## Pinterest pins", ""]
        for i, p in enumerate(extras["pins"], 1):
            out += [f"### Pin {i}: {p['angle']}", f"**Title ({len(p['title'])} chars):** {p['title']}", "",
                    f"**Description ({len(p['description'])} chars):** {p['description']}", "",
                    f"**Image direction:** {p['image_direction']}", ""]
    if extras.get("facebook"):
        out += ["## Facebook posts", ""]
        for i, f in enumerate(extras["facebook"], 1):
            out += [f"### Post {i} ({f['style']})", f["text"], ""]
    return "\n".join(out) + "\n"


def build_report(ctx: dict) -> str:
    art, primary, core, rows = ctx["article"], ctx["primary"], ctx.get("core"), ctx["checks"]
    s = checks.summarize(rows)
    na_note = f" ({s['na']} not testable for this file type)" if s["na"] else ""
    out = [f"# SEO report: {art.title or Path(art.path).name}",
           f"*File:* `{Path(art.path).name}` ({art.fmt}) | *Words:* {art.word_count:,} | *Run date:* {ctx['today']}",
           f"*Focus keyword:* **{primary}**" + (" (chosen by the agent: please confirm)" if ctx.get("keyword_suggested") else ""),
           "",
           "> Your article was not changed. These are suggestions for you to apply in WordPress / Rank Math.",
           "> Checks marked *approx* are the agent's approximation of Rank Math's test. Confirm in the plugin before publishing.",
           "",
           "## 1. Rank Math checklist",
           f"**Now (with the suggested SEO title, description and slug): {s['pass']} pass, {s['warn']} warn, "
           f"{s['fail']} fail** out of {s['total']} testable checks{na_note}."]
    after = ctx.get("checks_after")
    if after:
        sa = checks.summarize(after)
        out += [f"**After you also apply the suggested heading, intro and alt-text fixes: {sa['pass']} pass, "
                f"{sa['warn']} warn, {sa['fail']} fail.** (Predicted by the agent; Rank Math's own score is the final word.)"]
    out += ["", "| Group | Test | Now | After fixes | Detail |", "|---|---|---|---|---|"]
    after_by_test = {r["test"]: r for r in (after or [])}
    for r in rows:
        tag = ICON[r["status"]] + ("" if r["exact"] else " (approx)")
        a = after_by_test.get(r["test"])
        aft = "same" if (not a or a["status"] == r["status"]) else ICON[a["status"]] + ("" if a["exact"] else " (approx)")
        out.append(f"| {r['group']} | {r['test']} | {tag} | {aft} | {r['detail']} |")

    if core:
        out += ["", "## 2. SEO fields", "", "See `rank_math_fields.md` for copy-paste blocks.", "",
                f"- **Focus keyword(s):** {focus_keyword_field(primary, core.get('secondary_keywords', []))}",
                f"- **SEO title:** {core.get('seo_title') or 'Not generated'}",
                f"- **Meta description:** {core.get('meta_description') or 'Not generated'}",
                f"- **Slug:** {core.get('slug')}"]

        out += ["", "## 3. Keyword map", "", "| Keyword | Intent | Where to use | Note |", "|---|---|---|---|"]
        for k in core["keyword_map"]:
            out.append(f"| {k['keyword']} | {k['intent']} | {k['place']} | {k['note']} |")
        out += ["", "Search volume and difficulty are **Not verified**: they need a paid keyword tool."]

        fixes = core.get("heading_fixes") or []
        out += ["", "## 4. Suggested heading fixes"]
        if fixes:
            out += ["", "| Current heading | Suggested | Why |", "|---|---|---|"]
            out += [f"| {f['current']} | {f['suggested']} | {f['reason']} |" for f in fixes]
        else:
            out += ["", "None needed or none validated."]
        intro_failed = any(r["test"].startswith("Focus keyword in first 10%") and r["status"] == "fail" for r in rows)
        if intro_failed and core.get("intro_fix"):
            f = core["intro_fix"]
            out += ["", "**Intro fix** (the focus keyword is missing from the first 10% of the content):", "",
                    f"- Current: {f['current_sentence']}", f"- Suggested: {f['suggested']}"]

        out += ["", "## 5. Internal link suggestions"]
        if core.get("internal_links"):
            out += ["", "| Link this existing text | to this page | Section |", "|---|---|---|"]
            out += [f"| {l['anchor_text']} | {l['url']} ({l['page_title']}) | {l['section']} |" for l in core["internal_links"]]
        else:
            out += ["", "None. Add your posts to `site_pages.csv` to get suggestions."]

        if art.images and core.get("image_alts"):
            out += ["", "## 6. Image alt text", "", "| Image # | Suggested alt text |", "|---|---|"]
            out += [f"| {a['image_number']} | {a['alt']} |" for a in core["image_alts"]]

        out += ["", "## 7. FAQs"]
        if core.get("faqs"):
            out += ["", "Use Rank Math's FAQ block (`faq.html` has the text). Answers use only facts stated in your article.", ""]
            for f in core["faqs"]:
                out += [f"**{f['question']}**  ", f["answer"], ""]
        else:
            out += ["", "No FAQ could be supported by the article text."]

        out += ["", "## 8. Comparison table"]
        ct = core.get("comparison_table")
        if ct:
            out += ["", f"{ct['reason']} The table is in `comparison_table.html`. Cells with numbers that are not in the article read \"Not verified\".", "",
                    "| " + " | ".join(ct["columns"]) + " |", "|" + "---|" * len(ct["columns"])]
            out += ["| " + " | ".join(r) + " |" for r in ct["rows"]]
        else:
            out += ["", "Not added: the article does not compare enough products on shared attributes."]
    else:
        out += ["", "## 2. SEO fields", ctx.get("llm_note", "LLM step skipped."),
                "Only the content checks above could be run."]

    if ctx.get("extras") and (ctx["extras"].get("pins") or ctx["extras"].get("facebook")):
        out += ["", "## 9. Social concepts", "See `social.md`."]
    notes = (core or {}).get("notes", []) + ctx.get("notes", [])
    if notes:
        out += ["", "## Run notes", *[f"- {n}" for n in notes]]
    return "\n".join(out) + "\n"


def write_article_outputs(base: Path, folder: str, ctx: dict) -> Path:
    out = base / folder
    out.mkdir(parents=True, exist_ok=True)
    core, extras = ctx.get("core"), ctx.get("extras")
    (out / "seo_report.md").write_text(build_report(ctx), encoding="utf-8")
    (out / "rank_math_fields.md").write_text(rank_math_fields_md(ctx["article"], ctx["primary"], core, ctx["cfg"]),
                                             encoding="utf-8")
    if core and core.get("faqs"):
        (out / "faq.html").write_text(faq_html(core["faqs"]), encoding="utf-8")
    if core and core.get("comparison_table"):
        (out / "comparison_table.html").write_text(table_html(core["comparison_table"]), encoding="utf-8")
    if core and core.get("internal_links"):
        with open(out / "internal_links.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["anchor_text", "url", "page_title", "section"])
            w.writeheader()
            w.writerows(core["internal_links"])
    if extras and (extras.get("pins") or extras.get("facebook")):
        (out / "social.md").write_text(social_md(extras, ctx["primary"]), encoding="utf-8")
    (out / "seo_package.json").write_text(json.dumps({
        "file": Path(ctx["article"].path).name, "focus_keyword": ctx["primary"], "run_date": ctx["today"],
        "checks": ctx["checks"], "check_summary": checks.summarize(ctx["checks"]),
        "checks_after_fixes": ctx.get("checks_after"),
        "seo": {k: v for k, v in (core or {}).items() if not k.startswith("_")} or None, "extras": extras, "notes": ctx.get("notes", []),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
