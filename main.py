"""
main.py
-------
Rank Math-aware SEO for articles you ALREADY have. For each article in articles/:

  1. read it (.docx / .html / .md / .txt)
  2. pick the focus keyword (jobs.csv, or the agent suggests one)
  3. collect keyword ideas (keywords.csv from the research agent, else free Google autocomplete)
  4. ask Groq for SEO title, meta description, secondary keywords, keyword map, heading fixes,
     FAQs, comparison table, internal links, image alt text (all validated by Python)
  5. run the Rank Math-style checklist against the article + suggested fields
  6. optionally draft 3 Pinterest pins and 3 Facebook posts
  7. write the report and copy-paste files

Your article is never modified and nothing is published.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

import yaml

import checks
import exporters
import keywords as kw
import llm
import reader
import seo

ROOT = Path(__file__).parent
PAUSE = 3          # seconds between articles (free-tier rate limits)
SUPPORTED = {".docx", ".html", ".htm", ".md", ".markdown", ".txt"}


def load_config(path: Path = ROOT / "config.yaml") -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg.setdefault("site", {})
    cfg.setdefault("llm", {"model": "openai/gpt-oss-120b", "max_tokens": 6000})
    cfg.setdefault("rank_math", {})
    rm = cfg["rank_math"]
    rm.setdefault("title_max_chars", 60)
    rm.setdefault("meta_max_chars", 160)
    rm.setdefault("meta_min_chars", 110)
    rm.setdefault("min_words", 600)
    rm.setdefault("secondary_keywords", 3)
    cfg.setdefault("faq_count", 5)
    cfg.setdefault("extras", {})
    return cfg


def load_jobs(root: Path = ROOT) -> list:
    """jobs.csv rows (file, focus_keyword). If the file has no rows, every file in articles/ is processed."""
    jobs = []
    f = root / "jobs.csv"
    if f.exists():
        with open(f, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                name = (r.get("file") or "").strip()
                if name:
                    jobs.append({"file": name, "focus_keyword": (r.get("focus_keyword") or "").strip().lower()})
    if not jobs:
        art = root / "articles"
        if art.exists():
            jobs = [{"file": p.name, "focus_keyword": ""} for p in sorted(art.iterdir())
                    if p.suffix.lower() in SUPPORTED and not p.name.startswith(("~$", "."))]
    return jobs


def load_site_pages(root: Path = ROOT) -> list:
    f = root / "site_pages.csv"
    out = []
    if f.exists():
        with open(f, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                url, title = (r.get("url") or "").strip(), (r.get("title") or "").strip()
                if url:
                    out.append({"url": url, "title": title or url})
    return out


def load_keyword_pool(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            k = (r.get("keyword") or "").strip().lower()
            if k:
                out.append(k)
    return out


def process_article(job: dict, cfg: dict, site_pages: list, pool_file: list, root: Path, out_base: Path,
                    today: str) -> dict:
    path = root / "articles" / job["file"]
    notes = []
    if not path.exists():
        return {"file": job["file"], "status": "failed: file not found in articles/"}
    try:
        article = reader.read_article(path)
    except Exception as e:  # noqa: BLE001
        return {"file": job["file"], "status": f"failed: could not read file ({e})"}
    if article.word_count < 50:
        return {"file": job["file"], "status": "failed: almost no text found in the file"}

    # keyword pool
    pool = list(pool_file)
    if not pool:
        seed = job["focus_keyword"] or " ".join((article.title or "").split()[:6])
        if seed:
            pool = [r["keyword"] for r in kw.collect_keywords([seed], alphabet=False, max_total=40)]
            if not pool:
                notes.append("Google autocomplete returned nothing; keyword ideas come from the article only.")

    # focus keyword
    primary, suggested = job["focus_keyword"], False
    core = extras = None
    llm_note = None
    if not primary:
        try:
            primary, suggested = seo.suggest_focus_keyword(article, pool, cfg), True
        except Exception as e:  # noqa: BLE001
            llm_note = f"LLM step skipped: {e}."
            commercial = [k for k in pool if checks.has_phrase(article.body_text, k)]
            primary = commercial[0] if commercial else ""
            if not primary:
                return {"file": job["file"], "status": "failed: no focus_keyword in jobs.csv and the agent could not suggest one"}
            suggested = True
            notes.append("Focus keyword taken from keyword ideas that appear in the article. Please confirm it.")

    # LLM package
    if llm_note is None:
        try:
            core = seo.generate_core(article, primary, pool, site_pages, cfg)
        except llm.LLMUnavailable as e:
            llm_note = f"LLM step skipped: {e}."
        except Exception as e:  # noqa: BLE001
            llm_note = f"LLM step failed: {e}."
    if core is not None and (cfg.get("extras", {}).get("pinterest") or cfg.get("extras", {}).get("facebook")):
        try:
            extras = seo.generate_extras(article, primary, cfg)
        except Exception as e:  # noqa: BLE001
            notes.append(f"Social concepts skipped: {e}")
    if llm_note:
        notes.append(llm_note)

    rows = checks.run_checks(article, primary, (core or {}).get("secondary_keywords", []),
                             (core or {}).get("seo_title"), (core or {}).get("meta_description"),
                             (core or {}).get("slug"), cfg)
    rows_after = None
    if core is not None:
        core["_primary"] = primary
        after = seo.apply_suggestions(article, core)
        rows_after = checks.run_checks(after, primary, core.get("secondary_keywords", []), core.get("seo_title"),
                                       core.get("meta_description"), core.get("slug"), cfg)
    ctx = {"cfg": cfg, "today": today, "article": article, "primary": primary, "keyword_suggested": suggested,
           "core": core, "extras": extras, "checks": rows, "checks_after": rows_after, "notes": notes,
           "llm_note": llm_note}
    folder = checks.slugify(path.stem, 60) or "article"
    outdir = exporters.write_article_outputs(out_base, folder, ctx)
    s = checks.summarize(rows)
    return {"file": job["file"], "status": "success", "focus_keyword": primary, "folder": str(outdir),
            "pass": s["pass"], "warn": s["warn"], "fail": s["fail"], "total": s["total"],
            "after_pass": checks.summarize(rows_after)["pass"] if rows_after else None,
            "seo_title": (core or {}).get("seo_title") or "", "notes": len(notes) + len((core or {}).get("notes", []))}


def run(root: Path = ROOT, cfg: dict | None = None, out_base: Path | None = None, today: str | None = None) -> dict:
    cfg = cfg or load_config(root / "config.yaml")
    today = today or date.today().isoformat()
    out_base = out_base or root / "output"
    jobs = load_jobs(root)
    if not jobs:
        print("Nothing to do: put articles in articles/ (and optionally list them in jobs.csv).")
        return {"status": "failed: no articles", "results": []}
    site_pages = load_site_pages(root)
    pool = load_keyword_pool(root / cfg.get("keywords_file", "keywords.csv"))
    results = []
    for i, job in enumerate(jobs):
        if i:
            time.sleep(PAUSE)
        r = process_article(job, cfg, site_pages, pool, root, out_base, today)
        results.append(r)
        print(f"{r['file']}: {r['status']}" + (f"  ({r['pass']} pass / {r['warn']} warn / {r['fail']} fail)"
                                               if r["status"] == "success" else ""))

    out_base.mkdir(parents=True, exist_ok=True)
    with open(out_base / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "status", "focus_keyword", "seo_title", "pass", "warn", "fail", "folder"])
        for r in results:
            w.writerow([r["file"], r["status"], r.get("focus_keyword", ""), r.get("seo_title", ""),
                        r.get("pass", ""), r.get("warn", ""), r.get("fail", ""), r.get("folder", "")])
    ok = sum(1 for r in results if r["status"] == "success")
    status = "success" if ok == len(results) else ("partial" if ok else "failed")
    summary = {"status": status, "processed": ok, "total": len(results), "results": results}
    (root / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


if __name__ == "__main__":
    s = run()
    sys.exit(0 if s["status"] in ("success", "partial") else 1)
