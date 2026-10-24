# SEO Optimizer for Rank Math ($0)

Takes an article you **already have** and gives you back an **optimized copy**:

- **Corrected heading structure**: one H1, sections at H2, sub-points at H3, no skipped levels; headings improved with your keywords; headings added where the article already has an untitled block of content.
- **Keywords placed by category**: a focus keyword, two or three primary/secondary keywords, a few semantic keywords. The text is tuned to your **keyword-density target (default 1.7%)**.
- **Rank Math fields**: focus + secondary keywords, SEO title (focus keyword first, power word, number), URL slug, meta description.
- **Search intent and topical coverage**: what a person searching your focus keyword expects, which topics your article covers, partly covers or misses.
- **Rank Math-style checklist before and after**. Images and links are left out of the checklist and are not optimized (they are kept as they are).
- Optional: grammar and spelling fixes (`seo.grammar: true`).

Your original file is never changed. Nothing is published.

## Input: `jobs.csv`
```
file,focus_keyword,secondary_keywords,semantic_keywords,power_word
01_Draft_Stoppers.docx,door draft stopper,foam door seal; draft blocker for renters,cold air; uneven floor; renter friendly,Proven
```
| Column | Meaning |
|---|---|
| `file` | the article in `articles/` (`.docx`, `.html`, `.md` or `.txt`) |
| `focus_keyword` | **required**. The one keyword the article ranks for |
| `secondary_keywords` | 2-3 primary/secondary keywords, separated by `;`. Rank Math allows 5 keywords in total (focus + up to 4) |
| `semantic_keywords` | a few related phrases, separated by `;`. Worked into the text and headings, not entered in Rank Math |
| `power_word` | optional word that must appear in the SEO title (for example Proven, Ultimate). Left blank, the agent picks one |

If you leave secondary or semantic keywords blank, the agent suggests some from your article and marks them "suggested: confirm".

## Output (`output/<article>/`)
| File | Use |
|---|---|
| `optimized_article.docx` (or `.html` / `.md`) | the optimized copy. Read `changes.md` first, then publish |
| `rank_math_fields.md` | copy-paste: focus keywords, SEO title, permalink, meta description, H1 |
| `heading_structure.md` | the H1/H2/H3 tree before and after |
| `topical_coverage.md` | search intent, what searchers expect, covered / partial / missing topics with suggested headings and questions |
| `changes.md` | every rewritten paragraph, before and after, keywords in bold; and every edit the agent refused |
| `seo_report.md` | result summary, keywords by category, density tuning rounds, checklist before / after |
`output/summary.csv` lists every article with density and pass counts before and after.

## Setup
1. Put the files in the root of a **private** GitHub repo (your drafts are unpublished) and add the secret `GROQ_API_KEY` (Settings -> Secrets and variables -> **Actions**).
2. Put your articles in `articles/` and fill `jobs.csv`.
3. Actions -> **SEO Agent** -> Run workflow, then download **seo-results**. Open `output/summary.csv`, then each article's `seo_report.md`.
4. `config.yaml` holds `density_target` (1.7), `density_tolerance` (0.2), and the other limits.

## How it stays safe
The model proposes, Python decides. A rewritten paragraph is accepted only if it keeps **every number exactly**, adds **no new names**, keeps the **meaning** (word overlap with the original), stays about the **same length**, and **really adds** the asked keyword (never more than twice in one paragraph).
Headings must point at real headings, may contain a number only if the article has it, and a heading inserted above a block must describe the paragraphs right below it. At most two subheadings carry the exact focus keyword.
Paragraphs with images or links, tables and the images themselves are never rewritten. The agent does **not** write new sections: it cannot know facts you have not given it, so missing topics are listed, not invented.

## Honest limits
- **Density 1.7%** is the target you asked for. Rank Math's own documentation scores best at 1-1.5% and warns above 2.5%, so 1.7% is a little above its best band. Change `density_target` any time. The agent counts exact phrase matches of the focus and secondary keywords; Rank Math may count slightly differently, so confirm in the plugin.
- A **long focus keyword** needs many exact repeats to reach a density target and can read as stuffing. If the agent cannot reach the target without breaking a safety rule, it stops and says so in the report instead of forcing it.
- A rewritten paragraph loses inline formatting inside it (bold or italic words). Headings, untouched paragraphs, images, tables and links keep theirs.
- Checks marked *approx* (title and description length, power words, sentiment, long paragraphs) are the agent's approximation of Rank Math. The power-word and sentiment lists are the agent's own; Rank Math uses its own list, so a custom `power_word` may not be counted by Rank Math.
- A **Table of Contents** cannot be added here: add Rank Math's Table of Contents block in WordPress.
- Slug: built from the focus keyword. If the post is already published, keep its URL unless you add a redirect.
- Groq's free tier is rate limited: the agent waits and retries. If a request is rejected as too large, lower `llm.max_tokens`.
- Only the first 2,600 words of a very long article are shown to the model for the strategy step.

## Tests
`python tests/test_offline.py` (no network or API key). The model is faked (it follows the task lists, or misbehaves on purpose), so the tests prove the editing, safety rules, density loop and reports, not the quality of real Groq output. The first real run is the live test.
