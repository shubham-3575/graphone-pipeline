# GraphOne Intelligence Pipeline

Async, fault-tolerant ingestion pipeline for the GraphOne / FrontierAtlas AI Engineer trial task: startups, products,
research papers, jobs and news, extracted into canonical schemas, entity-resolved, and exported to Google Sheets.

**Design principle: deterministic first, LLM last, nothing without a source URL.** Structured sources need no LLM at all;
where an LLM is used its answer must be backed by a verbatim quote found in the fetched text, or it is discarded.
Full design: [`architecture.pdf`](architecture.pdf) (2 pages: scale strategy, 413/429 handling, freshness tracking, storage, anti-bot).

## Repository layout

```
src/
  http_client.py        async fetcher: global + per-host limits, exponential backoff + full jitter, Retry-After
  llm_orchestrator.py   multi-tier LLM chain, 413/429 handling, per-tier throttle, circuit-breaker cooldown, answer cache
  text_chunking.py      semantic truncation to a char budget (lead + densest paragraphs, order preserved)
  grounding.py          anti-hallucination: extracted values must appear in the source text
  dates.py              publication-date extraction/normalization (JSON-LD, meta, <time>, relative text, unix) + 24h check
  freshness.py          atomic dedup claim store (canonical URL + content fingerprint)
  entity_resolver.py    deterministic canonicalization (exact > squashed > fuzzy), mapping log
  schemas.py            pydantic models for STARTUP / PRODUCT / RESEARCH_PAPER / JOB / NEWS
  export_sheets.py      6-tab Google Sheets export + CSV backups
  pipelines/            papers.py  startups.py  products.py  jobs.py  news.py
scripts/                probe_sources.py  validate_outputs.py  build_architecture_pdf.py
data/seed_startups.json 50 known AI startups with aliases (entity-resolution seed)
tests/                  unit tests (no network needed)
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # Windows: copy .env.example .env   -> then fill in keys
pytest -q
```

`.env` (never committed): `GEMINI_API_KEY`, `GROQ_API_KEY` (a tier without a key is skipped),
`GITHUB_TOKEN` (raises GitHub API limit from 60 to 5000 req/h), optional per-provider `*_RPM` throttles and model names.

## Running

Each pipeline is independent and writes JSONL to `output/`.

```bash
python -m src.pipelines.startups --target 1100          # YC open dataset, AI companies, no LLM
python -m src.pipelines.papers   --target 1200          # arXiv + GitHub stars; resumable (skips saved arXiv IDs)
python -m src.pipelines.products --target 1100 --llm-budget 0   # Show HN launches + rule-based pricing (0 = no LLM calls)
python -m src.pipelines.jobs --reset                    # job boards + AI-company career pages, last 24h (run right before export)
python -m src.pipelines.news --reset                    # 5 news sources, last 24h (run right before export)
python scripts/resolver_eval.py                         # entity-resolution precision/recall on labeled cases
python scripts/rematch_products.py                      # re-apply the YC-domain startup match to products.jsonl (seconds)
python scripts/llm_extract_demo.py --n 30               # live LLM check + independent accuracy check of the pricing rules
python scripts/validate_outputs.py                      # QA: counts, freshness, evidence, duplicates
python -m src.export_sheets --sheet-url "<your sheet url>"   # or --csv-only
```

Jobs and news are time-window data: run them last, with `--reset`, so every item is inside the 24-hour window at export.

### Google Sheets export (one-time setup)
1. Google Cloud: create a project, enable the **Sheets API** and **Drive API**.
2. Create a service account, add a JSON key, save as `service_account.json` in the project root (git-ignored).
3. Create an empty Google Sheet, share it with the service account e-mail as **Editor**, and set General access to
   *Anyone with the link: Viewer*.
4. `python -m src.export_sheets --sheet-url "<sheet url>"` writes the tabs: Startups, Products, Research Papers, Jobs, News,
   Entity Mapping Log (plus CSV backups in `output/csv/`).

## How each phase is covered

| Phase | Implementation |
|---|---|
| I  Bulk acquisition | `pipelines/startups.py` (YC dataset), `papers.py` (arXiv API, repo URL from the paper's own abstract/comment, **current stars from the GitHub API with read timestamp**), `products.py` (Show HN discovery, product site + pricing page). Resumable, idempotent, sharded by offset/window, so scaling is infrastructure, not code. |
| II Freshness | `jobs.py` (7 sources monitored; 5 had fresh jobs when measured), `news.py` (5 feeds, full-text extraction); `dates.py` handles JSON-LD/meta/`<time>`, relative dates ("2 hours ago", "yesterday"), unix times, time zones; `freshness.py` = first-seen heuristic and atomic dedup. |
| III LLM engine | `llm_orchestrator.py`: Gemini main > Gemini lite > Groq large > Groq small (any OpenAI-compatible provider can be added as another tier). 429: Retry-After / exponential backoff + jitter, per-tier request spacing, cooldown breaker. 413 / context overflow: `text_chunking.py` + budget halving. Persistent answer cache. |
| IV Entity resolution | `entity_resolver.py`: normalize + strip legal suffixes, exact alias, squashed ("Open AI" = "OpenAI"), conservative fuzzy with an ambiguity gap; unknown names are kept as new entities, never forced onto a wrong canonical. Every decision is logged (raw, canonical, method, score). `scripts/resolver_eval.py` scores it on 59 labeled messy variants and 15 look-alike names that must not merge: 59/59 correct, 15/15 left unmerged, 100% precision (synthetic cases, labeled `origin=synthetic-eval` in the sheet; real pipeline decisions are `origin=pipeline`). |
| V Anti-bot / scale | All I/O is asyncio + aiohttp. Strategy and trial observations are in `architecture.pdf` section 6. |
| VI Architecture | `architecture.pdf`. |

## Data-quality guarantees

- Every record has a valid `http(s)` `source.url` (schema-enforced); invalid records are rejected, never written.
- No LLM is involved for startups, papers, jobs or news fields. For product pricing, the rules store the matched quote as
  `pricingEvidence`; if the LLM is used, its evidence quote must appear verbatim in the page text or the value is nulled.
- Jobs and news are checked against the 24h window at collection, and again by `scripts/validate_outputs.py`.
- Papers include `github_stars_fetched_at`, because stars are a moving metric.

## Assumptions and known limitations (stated honestly)

- **Papers with Code is defunct** (shut down July 2025); papers come from arXiv, with repo links taken from the paper's own text
  and stars from GitHub. Papers whose repo cannot be read are dropped, not guessed.
- **Startups** come from the open YC dataset (AI-related, active); `employeeCount` is YC's team size and is empty when YC lists none.
- **Products** are Show HN launches that have their own website. `startupName` is matched by YC domain, then known names, else
  the product's own brand (`startupMatch` column says which). Pricing is read from the product's site by keyword rules (a published price wins over a "contact sales" tier; a weak signal such as "start for free" alone is NOT enough and leaves the field empty); some pages
  mislead a keyword rule (e.g. a directory site listing other tools' prices), and pricing stays empty when the page does not decide.
- **Jobs:** sources monitored: Arbeitnow, Jobicy, Himalayas, We Work Remotely (all-categories feed), RemoteOK, Remotive, and 18 AI-company career pages on Ashby's public API. Measured on 2026-11-23, five of them had jobs in the last 24 h (Arbeitnow, Jobicy, Himalayas, We Work Remotely, Ashby); RemoteOK's newest job was 24.8 h old and Remotive's 83.9 h old, so they contributed nothing. Lever career pages were tried and dropped (none reachable). Arbeitnow, Jobicy, Himalayas and We Work Remotely are general boards, so every job carries an `ai_related` flag (AI-company page, or AI/ML keyword in the title; the title check is crude); use `--ai-only` to keep only flagged jobs. Timestamps without a time zone are treated as UTC. RemoteOK, Remotive, We Work Remotely and Jobicy are remote-only
  boards, so `is_remote` is true for them. `role_family` comes from title keyword rules.
- **News sources** were chosen from measured 24h volume. Evaluated and dropped: MIT Technology Review, Ars Technica, Google AI Blog
  (nothing fresh in the window), Hugging Face Blog, WIRED (1 item), VentureBeat (HTTP 429), AI News (HTTP 403).
  Where an article page blocks us (403) we fall back to the feed's own text, and drop the item if no readable full text exists.
- **LLM usage in the submitted data:** the products job ran with `--llm-budget 0`, so every pricing value in the Products tab comes from the deterministic keyword rules, and no record in any tab was produced by an LLM. The LLM engine is implemented and unit-tested offline, and `scripts/llm_extract_demo.py` runs it live on a sample of real product pages and compares its answers with the rules.
- **Live LLM check (one run, 30 real product pages, free-tier keys):** Groq answered 30/30 (18 by `groq-large`, 12 by `groq-small`). 17 HTTP 429 responses were handled with backoff, and 2 requests that exhausted their retries on `groq-large` fell back to `groq-small`. The grounding guard nulled 2 answers whose quoted evidence was not in the page. **Both Gemini tiers were disabled by an auth/model error in that run and were not verified working**; the cause was not diagnosed, so the Gemini path is untested live (the chain continued on Groq).
- **Pricing label accuracy is not established:** on those 30 pages the rules and the LLM agreed on 17 of 26 comparable cases (65%). Reading the disagreements, neither side is consistently right (for example "Start Free Trial" next to a price can be mislabeled FREEMIUM by the rules, and the LLM can answer FREE from a page that also lists paid plans). Treat the Pricing column as a best-effort keyword label with its evidence quote, not as ground truth.
- **Pricing accuracy** of the rules has not been measured against ground truth. FREEMIUM is the largest class; a published price plus any free-entry wording is labeled FREEMIUM. Check `pricingEvidence` to audit any row.
- **News full text** is extracted from the article page (trafilatura) or taken from the feed when the page blocks us; extraction can include site boilerplate (e.g. a subscription box on The Decoder).
- A browser-based (Playwright) fetcher for hard anti-bot targets is designed in `architecture.pdf` but not implemented in this trial.
- Unit tests run offline; the live paths were exercised by running the pipelines.

## Tests

```bash
pytest -q
```
