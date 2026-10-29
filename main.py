"""
main.py
-------
SEO optimizer for Rank Math. For each article in articles/ (listed in jobs.csv with its keywords):

  1. read the article and run the Rank Math-style checklist ("before")
  2. strategy: search intent, new H1, SEO title, meta description, corrected H1/H2/H3 structure, topical-coverage review
  3. keyword placement and density tuning to your target, with every rewrite checked by Python
  4. save an OPTIMIZED COPY (your original is never changed) and re-read that copy to run the checklist again ("after")
  5. write the Rank Math fields and the reports

Images and links are not optimized (they are kept as they are). Nothing is published.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import yaml

import checks
import doc as docmod
import edits
import exporters
import llm
import reader
import strategy

ROOT = Path(__file__).parent
PAUSE = 3          # seconds between articles (free-tier rate limits)
SUPPORTED = {".docx", ".html", ".htm", ".md", ".markdown", ".txt"}


def load_config(path: Path = ROOT / "config.yaml") -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg.setdefault("site", {})
    cfg.setdefault("llm", {"model": "openai/gpt-oss-120b", "max_tokens": 6000})
    cfg.setdefault("seo", {})
    for k, v in {"density_target": 1.7, "density_tolerance": 0.2, "max_edit_rounds": 3, "similarity_min": 0.6,
                 "grammar": False, "skip_tests": ["images", "links"]}.items():
        cfg["seo"].setdefault(k, v)
    cfg.setdefault("rank_math", {})
    for k, v in {"title_max_chars": 60, "meta_max_chars": 160, "meta_min_chars": 110, "min_words": 600, "secondary_keywords": 3}.items():
        cfg["rank_math"].setdefault(k, v)
    return cfg


def parse_list(text) -> list:
    out, seen = [], set()
    for k in re.split(r"[;|,\n]", text or ""):
        k = re.sub(r"\s+", " ", k.strip().lower())
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


COLUMNS = ["file", "focus_keyword", "secondary_keywords", "semantic_keywords", "power_word"]
EXT_ORDER = {".docx": 0, ".html": 1, ".htm": 1, ".md": 2, ".markdown": 2, ".txt": 3}


def _norm_col(h: str) -> str:
    return re.sub(r"[\s-]+", "_", h.strip().strip("\"'").lower())


def load_jobs(root: Path = ROOT) -> list:
    """jobs.csv. Columns: file, focus_keyword, secondary_keywords, semantic_keywords, power_word.

    Accepted styles (spreadsheets in some regions save CSV with semicolons):
      * comma columns:      01_article_1,seals gaps,foam seal; draft blocker,cold air
      * semicolon columns:  01_article_1;seals gaps;keep your house warm;removable under door seal
    Values map to the columns in order. A header row is optional-looking but must name the columns; the file name may omit its extension.
    A row with MORE values than there are columns is refused with an explanation instead of being misread."""
    jobs = []
    f = root / "jobs.csv"
    if f.exists():
        lines = [l for l in f.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        header = [_norm_col(h) for h in re.split(r"[;,\t]", lines[0])] if lines else []
        if header and "file" not in header:                      # no header line: treat every line as a job, columns in the usual order
            header, data = list(COLUMNS), list(enumerate(lines, start=1))
        else:
            data = list(enumerate(lines[1:], start=2))
        for n, line in data:
            row = next(csv.reader([line]))
            if len(row) == 1 and ";" in row[0]:                  # semicolon-style row under a comma header (or the reverse)
                row = next(csv.reader([row[0]], delimiter=";"))
            if len(row) > len(header):
                jobs.append({"file": (row[0].strip() or f"line {n}"), "error": (
                    f"jobs.csv line {n} has {len(row)} values but the header has {len(header)} columns. "
                    "Use one value per column: file, focus_keyword, secondary_keywords, semantic_keywords, power_word. "
                    "If one column holds several keywords, separate them with | (or put the cell in double quotes when you use commas).")})
                continue
            r = dict(zip(header, row + [""] * (len(header) - len(row))))
            name = (r.get("file") or "").strip().strip("\"'")
            if name:
                jobs.append({"file": name, "focus_keyword": (r.get("focus_keyword") or "").strip().lower(),
                             "secondary": parse_list(r.get("secondary_keywords")), "semantic": parse_list(r.get("semantic_keywords")),
                             "power_word": (r.get("power_word") or "").strip()})
    if not jobs:
        art = root / "articles"
        if art.exists():
            jobs = [{"file": p.name, "focus_keyword": "", "secondary": [], "semantic": [], "power_word": ""}
                    for p in sorted(art.iterdir()) if p.suffix.lower() in SUPPORTED and not p.name.startswith(("~$", "."))]
    return jobs


def resolve_article(root: Path, name: str):
    """Find the article file. The extension may be left out, and the case may differ. Returns (path or None, hint)."""
    d = root / "articles"
    files = [x for x in d.iterdir() if x.is_file() and x.suffix.lower() in SUPPORTED and not x.name.startswith(("~$", "."))] if d.exists() else []
    if (d / name).is_file():
        return d / name, ""
    low = name.lower()
    exact = [x for x in files if x.name.lower() == low]
    if exact:
        return exact[0], ""
    stem = Path(name).stem.lower() if Path(name).suffix.lower() in SUPPORTED else low
    cand = sorted((x for x in files if x.stem.lower() == stem), key=lambda x: EXT_ORDER[x.suffix.lower()])
    if cand:
        return cand[0], ""
    have = ", ".join(sorted(x.name for x in files)) or "(the folder is empty)"
    return None, f"'{name}' was not found in articles/. Files there: {have}"


def heading_changes(document) -> list:
    out = []
    for b in document.blocks:
        for lvl, text in b.insert_before:
            out.append(("(inserted)", f"H{lvl}: {text}"))
        if b.kind in ("title", "h") and (b.new_text is not None or b.new_level is not None):
            a, z = f"H{b.level}: {b.text}", f"H{b.cur_level}: {b.cur_text}"
            if a != z:
                out.append((a, z))
    return out


def density_of(article, kw: dict) -> float:
    wc = article.word_count
    total = sum(checks.count_phrase(article.body_text, k) for k in [kw["focus"]] + kw["secondary"])
    return round(100 * total / wc, 2) if wc else 0.0


def process_article(job: dict, cfg: dict, root: Path, out_base: Path, today: str) -> dict:
    name = job["file"]
    if job.get("error"):
        return {"file": name, "status": "failed: " + job["error"]}
    path, hint = resolve_article(root, name)
    if path is None:
        return {"file": name, "status": "failed: " + hint}
    if not job["focus_keyword"]:
        return {"file": name, "status": "failed: put the focus_keyword for this article in jobs.csv"}
    try:
        article_before = reader.read_article(path)
        document = docmod.load(path)
    except Exception as e:  # noqa: BLE001
        return {"file": name, "status": f"failed: could not read file ({e})"}
    if article_before.word_count < 50:
        return {"file": name, "status": "failed: almost no text found in the file"}

    focus = job["focus_keyword"]
    secondary = [k for k in job["secondary"] if k != focus][:4]
    semantic = [k for k in job["semantic"] if k != focus and k not in secondary]
    kw = {"focus": focus, "secondary": secondary, "semantic": semantic, "power_word": job["power_word"] or None}
    notes = []
    if len(job["secondary"]) > 4:
        notes.append("Rank Math allows 5 keywords in total, so only the first 4 secondary keywords are used.")

    rows_before = checks.run_checks(article_before, focus, secondary, None, None, None, cfg)
    folder = checks.slugify(path.stem, 60) or "article"
    outdir = out_base / folder
    outline_before = document.outline()

    # ---- 2. strategy
    try:
        plan = strategy.generate_strategy(document, kw, cfg)
    except llm.LLMUnavailable as e:
        exporters.write_audit_only(outdir, article_before, kw, rows_before, f"{e}. Add GROQ_API_KEY as an Actions secret.", today)
        return {"file": name, "status": "audit only: GROQ_API_KEY is not set", "folder": str(outdir)}
    except Exception as e:  # noqa: BLE001
        exporters.write_audit_only(outdir, article_before, kw, rows_before, f"the AI step failed ({e}).", today)
        return {"file": name, "status": f"failed: AI step ({str(e)[:150]})", "folder": str(outdir)}

    suggested = []
    if plan["suggested_secondary"]:
        kw["secondary"] = plan["suggested_secondary"]
        suggested += kw["secondary"]
    if plan["suggested_semantic"]:
        kw["semantic"] = plan["suggested_semantic"]
        suggested += kw["semantic"]
    if suggested:
        notes.append("You gave no " + " / ".join(x for x, f in (("secondary", job["secondary"]), ("semantic", job["semantic"])) if not f)
                     + " keywords, so the agent suggested some from your article. Confirm them: " + ", ".join(suggested))
    structure_notes = strategy.apply_strategy(document, plan)
    outline_after = document.outline()
    heads_text = [t for lvl, t in outline_after if lvl > 1]
    for k in kw["secondary"]:
        if not any(checks.has_phrase(t, k) for t in heads_text):
            notes.append(f"The secondary keyword '{k}' is not in any heading because no heading fit it naturally. "
                         "Consider renaming one section so it appears once in an H2 or H3.")

    # ---- 3. keywords and density
    try:
        edit_rep = edits.optimize_body(document, kw, cfg)
    except llm.LLMUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        edit_rep = {"history": [{"round": 0, "density": 0.0, "count": 0, "words": article_before.word_count}], "edits": [], "rejected": [],
                    "notes": [], "final": None}
        notes.append(f"The keyword-editing step failed ({e}); headings and SEO fields are still provided.")
    grammar = None
    if cfg["seo"].get("grammar"):
        try:
            grammar = edits.grammar_pass(document, kw, cfg)
        except Exception as e:  # noqa: BLE001
            notes.append(f"Grammar pass failed ({e}).")

    # ---- 4. save the copy, then judge the saved file itself
    out_path = docmod.save(document, outdir, "optimized_article")
    article_after = reader.read_article(out_path)
    rows_after = checks.run_checks(article_after, focus, kw["secondary"], plan.get("seo_title"), plan.get("meta_description"), plan["slug"], cfg)
    final_density = density_of(article_after, kw)

    notes += plan["notes"] + structure_notes + edit_rep["notes"]
    tgt, tol = cfg["seo"]["density_target"], cfg["seo"]["density_tolerance"]
    if abs(final_density - tgt) > tol:
        notes.append(f"Density reached {final_density}% against a target of {tgt}% (+/- {tol}). The agent will not force keywords into paragraphs where they do not fit "
                     "or break its safety rules. Add more relevant text, or adjust density_target in config.yaml.")
    if edit_rep["rejected"]:
        notes.append(f"{len(edit_rep['rejected'])} proposed rewrite(s) were refused by the safety rules (see changes.md).")
    if not checks.has_phrase(checks.first_10_percent(article_after.words), focus):
        notes.append("The focus keyword is still not in the first 10% of the text: the first paragraph could not be edited.")
    if article_after.fmt == "docx" and article_before.images:
        notes.append("Images were kept as they are. Re-add their alt text in WordPress after upload.")

    ctx = {"cfg": cfg, "today": today, "kw": kw, "plan": plan, "edits": edit_rep, "grammar": grammar,
           "article_before": article_before, "article_after": article_after, "rows_before": rows_before, "rows_after": rows_after,
           "outline_before": outline_before, "outline_after": outline_after, "heading_changes": heading_changes(document),
           "structure_notes": structure_notes, "final_density": final_density, "suggested": suggested, "notes": notes}
    ctx["kw_rows"] = exporters.keyword_rows(ctx)
    exporters.write_reports(outdir, ctx)
    sb, sa = checks.summarize(rows_before), checks.summarize(rows_after)
    return {"file": name, "status": "success", "focus_keyword": focus, "folder": str(outdir),
            "density_before": edit_rep["history"][0]["density"], "density_after": final_density,
            "pass_before": sb["pass"], "fail_before": sb["fail"], "pass_after": sa["pass"], "fail_after": sa["fail"], "notes": len(notes)}


def run(root: Path = ROOT, cfg: dict | None = None, out_base: Path | None = None, today: str | None = None) -> dict:
    cfg = cfg or load_config(root / "config.yaml")
    today = today or date.today().isoformat()
    out_base = out_base or root / "output"
    jobs = load_jobs(root)
    if not jobs:
        print("Nothing to do: put articles in articles/ and list them in jobs.csv with their keywords.")
        return {"status": "failed: no articles", "results": []}
    results = []
    for i, job in enumerate(jobs):
        if i:
            time.sleep(PAUSE)
        try:
            r = process_article(job, cfg, root, out_base, today)
        except llm.LLMUnavailable as e:
            r = {"file": job["file"], "status": f"failed: {e}"}
        results.append(r)
        extra = ""
        if r["status"] == "success":
            extra = f"  (density {r['density_before']}% -> {r['density_after']}%, checks {r['pass_before']} -> {r['pass_after']} pass)"
        print(f"{r['file']}: {r['status']}{extra}")

    out_base.mkdir(parents=True, exist_ok=True)
    cols = ["file", "status", "focus_keyword", "density_before", "density_after", "pass_before", "fail_before", "pass_after", "fail_after", "folder"]
    with open(out_base / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in results:
            w.writerow([r.get(c, "") for c in cols])
    ok = sum(1 for r in results if r["status"] == "success")
    status = "success" if ok == len(results) else ("partial" if ok else "failed")
    summary = {"status": status, "processed": ok, "total": len(results), "results": results}
    (root / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


if __name__ == "__main__":
    s = run()
    sys.exit(0 if s["status"] in ("success", "partial") else 1)
