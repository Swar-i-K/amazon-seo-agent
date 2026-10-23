# SEO Agent for Rank Math ($0)

Does the on-page SEO for articles you **already have**. It does not write or rewrite articles, and it never publishes.
For each article it produces what you paste into Rank Math plus a checklist that mirrors Rank Math's tests.

## What you get per article (`output/<article>/`)
| File | Use |
|---|---|
| `rank_math_fields.md` | Copy-paste: Focus Keyword(s), SEO Title, Permalink, Meta Description |
| `seo_report.md` | Rank Math checklist (**now** vs **after the suggested fixes**), keyword map, heading fixes, intro fix, internal links, image alt text, FAQs, comparison table |
| `faq.html` | FAQ text for Rank Math's FAQ block |
| `comparison_table.html` | Table for a Custom HTML block (only if the article really compares products) |
| `internal_links.csv` | Which existing text to link to which of your pages |
| `social.md` | 3 Pinterest pin concepts + 3 Facebook posts (optional, switch off in `config.yaml`) |
| `seo_package.json` | Everything, machine-readable |
`output/summary.csv` lists all articles with pass/warn/fail counts.

## Setup (once)
1. Put these files in the **root** of a GitHub repo. If your articles are unpublished drafts, make the repo **private**.
2. Repo -> Settings -> Secrets and variables -> Actions -> add `GROQ_API_KEY` (same key as your other agents).
3. Put your articles in `articles/` (`.docx`, `.html`, `.md` or `.txt`). Your article agent's `.docx` files work as they are.
4. Optional but recommended:
   - `jobs.csv`: `file,focus_keyword` (the focus keyword you want for each article). Leave the keyword blank and the agent suggests one for you to confirm. Leave the whole file empty and every file in `articles/` is processed.
   - `site_pages.csv`: `url,title` of your existing posts, so it can suggest internal links (it only uses URLs from this list).
   - `keywords.csv`: the research agent's keyword file. Without it, free Google autocomplete is used.
   - `config.yaml`: put your site address in `site.domain` so internal and external links are told apart in HTML files.

## Run
Actions -> **SEO Agent** -> Run workflow, then download the **seo-results** artifact. Start with `output/summary.csv`.

## Applying it in WordPress
Paste the four values from `rank_math_fields.md` into the Rank Math panel, make the heading and intro fixes you agree with, add the FAQ block
and Table of Contents block, add the internal links, then let Rank Math's own score confirm.
**Already-published post? Keep its URL.** Changing a live slug without a redirect loses links and rankings.

## Honest limits
- Checks marked *approx* are my approximation of Rank Math (it measures title/description by pixel width, uses its own power-word and
  sentiment lists, and may match keyword variations differently). Thresholds come from Rank Math's documentation (600+ words, density 1-1.5%,
  flag above 2.5%, paragraphs over about 120 words, keyword in title/description/URL/first 10%/subheadings). Rank Math's own score is the final word.
- **Word files from the article agent have no hyperlinks**, so the internal/external link tests show "n/a" for them. Check links in WordPress.
- Alt text set in Word does not survive into WordPress: add it again after upload.
- The model only suggests. Python then drops heading fixes that do not match a real heading, internal links whose URL is not in `site_pages.csv`
  or whose anchor text is not in the article, and any number in a FAQ or table cell that the article does not contain.
  FAQ answers use only facts in your article. If the article does not support an answer, there is no FAQ for it.
- The agent builds the slug itself from the focus keyword. If the SEO title cannot be fixed to Rank Math's rules after two repair tries, it says so in the report.
- No search volume or keyword difficulty (needs a paid tool): they read "Not verified".
- FAQ schema: use Rank Math's FAQ block. Do not also paste JSON-LD, or the page gets duplicate schema.
- Social drafts use only facts in the article, no prices, ratings or review counts. Link to your article, which carries your affiliate disclosure.
- Groq's free tier is rate limited: the agent waits and retries, so a run with many articles can take a few minutes.

## Tests
`python tests/test_offline.py` (no network or API key). They use canned model answers, so they prove the reading, checking, validation and export
logic, not the quality of real Groq output. The first real run is the live test.
