"""Builds architecture.pdf (max 3 pages).  python scripts/build_architecture_pdf.py"""
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether

INK, MUTED, ACC, SOFT = colors.HexColor("#1b2430"), colors.HexColor("#5b6675"), colors.HexColor("#1f5fbf"), colors.HexColor("#eef3fb")
B = ParagraphStyle("b", fontName="Helvetica", fontSize=8.4, leading=11.2, textColor=INK, alignment=TA_LEFT, spaceAfter=2.5)
BL = ParagraphStyle("bl", parent=B, leftIndent=9, bulletIndent=0, spaceAfter=1.6)
H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=INK, spaceAfter=1)
SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=8.6, leading=11, textColor=MUTED, spaceAfter=6)
H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=10.4, leading=13, textColor=ACC, spaceBefore=7, spaceAfter=2.5)
SM = ParagraphStyle("sm", parent=B, fontSize=7.6, leading=10, textColor=MUTED)


def P(t): return Paragraph(t, B)
def L(items): return [Paragraph(i, BL, bulletText="•") for i in items]


def diagram():
    import textwrap
    W, H = 17.8 * cm, 8.3 * cm
    d = Drawing(W, H)
    bw, bh, gap = 3.9 * cm, 2.3 * cm, 0.73 * cm
    y1, y2 = 5.55 * cm, 1.45 * cm
    AMBER = colors.HexColor("#fff4de")
    row1 = [("1  Sources", "APIs, RSS feeds, open datasets, web pages"),
            ("2  Async fetch", "one shared client: rate limits, retries, backoff"),
            ("3  Parse (no LLM)", "API fields, RSS fields, JSON-LD, keyword rules"),
            ("4  LLM extract", "only for pages with no structure: Gemini, then Groq")]
    row2 = [("5  Verify", "quoted evidence must appear in the source text; schema and source URL required"),
            ("6  Resolve entities", "'Open AI' becomes 'OpenAI' (exact, squashed, fuzzy match)"),
            ("7  Dedup and freshness", "atomic claim on URL + content fingerprint; 24 h window"),
            ("8  Store and export", "JSONL files, then Google Sheet and CSV")]

    def box(x, y, title, sub, fill=SOFT, dash=None):
        d.add(Rect(x, y, bw, bh, rx=5, ry=5, fillColor=fill, strokeColor=ACC, strokeWidth=0.9, strokeDashArray=dash))
        d.add(String(x + bw / 2, y + bh - 0.55 * cm, title, fontName="Helvetica-Bold", fontSize=8.4, fillColor=INK, textAnchor="middle"))
        for i, line in enumerate(textwrap.wrap(sub, 31)[:4]):
            d.add(String(x + bw / 2, y + bh - 1.05 * cm - i * 8.6, line, fontName="Helvetica", fontSize=6.9, fillColor=MUTED, textAnchor="middle"))

    def arrow(x1, y1_, x2, y2_, dash=None):
        d.add(Line(x1, y1_, x2, y2_, strokeColor=ACC, strokeWidth=1.1, strokeDashArray=dash))

    def head(x, y, direction):
        pts = {"r": [x - 4, y + 2.6, x, y, x - 4, y - 2.6], "d": [x - 2.6, y + 4, x, y, x + 2.6, y + 4]}[direction]
        d.add(Polygon(pts, fillColor=ACC, strokeColor=ACC))

    xs = [i * (bw + gap) for i in range(4)]
    for i, (t, sub) in enumerate(row1):
        box(xs[i], y1, t, sub, fill=AMBER if i == 3 else SOFT, dash=[3, 2] if i == 3 else None)
    for i, (t, sub) in enumerate(row2):
        box(xs[i], y2, t, sub)
    for i in range(3):                                          # row arrows
        arrow(xs[i] + bw, y1 + bh / 2, xs[i + 1], y1 + bh / 2); head(xs[i + 1], y1 + bh / 2, "r")
        arrow(xs[i] + bw, y2 + bh / 2, xs[i + 1], y2 + bh / 2); head(xs[i + 1], y2 + bh / 2, "r")
    ym = y2 + bh + (y1 - (y2 + bh)) / 2                         # bus between the rows
    c3, c4, c5 = xs[2] + bw / 2, xs[3] + bw / 2, xs[0] + bw / 2
    arrow(c4, y1, c4, ym)                                       # step 4 -> bus
    arrow(c3, y1, c3, ym, dash=[3, 2])                          # step 3 -> bus (structured data skips step 4)
    arrow(c4, ym, c5, ym)                                       # bus -> above step 5
    arrow(c5, ym, c5, y2 + bh); head(c5, y2 + bh, "d")
    d.add(String(c3 - 0.15 * cm, ym + 3, "structured sources skip step 4", fontName="Helvetica-Oblique", fontSize=6.6, fillColor=MUTED, textAnchor="end"))
    d.add(String((c5 + c3) / 2 - 1.3 * cm, ym - 9, "every record continues with step 5", fontName="Helvetica-Oblique", fontSize=6.6, fillColor=MUTED, textAnchor="middle"))
    d.add(Rect(0, 0.1 * cm, W, 1.0 * cm, rx=5, ry=5, fillColor=colors.white, strokeColor=MUTED, strokeWidth=0.6, strokeDashArray=[2, 2]))
    d.add(String(W / 2, 0.72 * cm, "At any step, a failure sends the item to a retry list and the log; its fields are never filled in by guesswork.",
                 fontName="Helvetica-Bold", fontSize=6.9, fillColor=INK, textAnchor="middle"))
    d.add(String(W / 2, 0.3 * cm, "Caches avoid repeat cost: LLM answers (sha256 of the prompt) and already-processed product pages.",
                 fontName="Helvetica", fontSize=6.7, fillColor=MUTED, textAnchor="middle"))
    return d


story = [
    Paragraph("GraphOne Intelligence Pipeline: Architecture &amp; Production Design", H1),
    Paragraph("Async ingestion of startups, products, papers, jobs and news. Design principle: <b>deterministic first, LLM last, nothing without a source URL.</b>", SUB),
    diagram(),
    Paragraph("How to read the diagram: follow steps 1 to 4 on the top row, then 5 to 8 on the bottom row. A record from a structured source (an API or feed) skips step 4; only unstructured pages go through the LLM.", SM),
    Paragraph("1. What is built (trial) vs. what is designed (production)", H2),
    P("The trial pipeline is a set of asyncio/aiohttp workers sharing one HTTP client (global + per-host concurrency caps, retries with exponential backoff and full jitter, <i>Retry-After</i> honoured). "
      "Each source has a small adapter that yields a normalized dict; shared code then applies the 24h freshness window, atomic dedup, entity resolution and pydantic schema validation. "
      "Structured sources (YC dataset, arXiv API, GitHub REST, job APIs, RSS) need <b>no LLM at all</b>, so those records cannot be hallucinated. The LLM engine is meant for pages with no structure (e.g. a product's pricing model), "
      "and its answer is accepted only if the quoted evidence appears verbatim in the fetched text. In the submitted dataset every product pricing value came from the deterministic keyword rules (the job ran with the LLM budget set to 0); the LLM path is unit-tested and is exercised live, on a sample of real pages, by <i>scripts/llm_extract_demo.py</i> (results in the README). Every record carries <i>source.url</i>; records without a valid URL are rejected at the schema layer."),
    P("<b>Built in the trial:</b> the async fetch layer, all five pipelines, the LLM engine with its 413/429 handling, grounding, date normalization, the atomic dedup store (SQLite), entity resolution, and the Sheets/CSV export. "
      "<b>Designed, not built:</b> the durable task queue and autoscaled workers, Redis-shared rate-limit buckets, the PostgreSQL / pgvector / graph storage, and the browser-based fetcher for protected sites (sections 2, 5 and 6)."),
    Paragraph("2. Scale strategy: 500,000+ startups, products and papers without manual intervention", H2),
    *L(["<b>Seeding in layers.</b> (a) Bulk datasets and APIs paged by window or cursor (arXiv by date range, GitHub search by star range, directory datasets); (b) sitemap indexes sharded by URL hash; (c) link expansion from each startup's site to its product, pricing and careers pages; (d) RSS/changefeeds and ETag/<i>If-Modified-Since</i> polling for incremental updates.",
        "<b>Task queue, not loops.</b> Work is a task <i>(source, shard, cursor)</i> on a durable queue (SQS / Kafka / Redis Streams). Tasks are idempotent, leased with a visibility timeout, and the cursor is checkpointed after each page. A crashed worker loses at most one page, and re-running is safe: the trial papers job already resumes by arXiv ID and the products job by cached URL.",
        "<b>Stateless, autoscaled workers.</b> Containers on ECS/Kubernetes scale on queue depth. Politeness is enforced centrally: a Redis token bucket per host and per source, so adding workers raises throughput only where a source allows it. Because per-host limits are the real ceiling (e.g. 2 req/s on one host = ~70 h for 500k pages), the plan is to spread across many hosts and prefer bulk APIs and datasets over page crawling. These are planning estimates, not measurements.",
        "<b>Cost and quality control.</b> Parsers and rules run first; in an early test the pricing rules decided about one third of candidate product pages (60 of 179), so candidates are over-collected and the pages the rules cannot decide are left empty or sent to the LLM, behind a persistent answer cache. Dashboards track success rate per source/status code and null-rate drift per field, which is the early signal for a changed page layout. Exhausted items go to a dead-letter queue for replay."]),
    Paragraph("3. Handling 413s and 429s across thousands of concurrent extractions", H2),
    *L(["<b>413 / context overflow.</b> (1) Strip to main content before the model sees it (trafilatura / HTML to text). (2) Each tier has a conservative input budget in characters (Groq 12-16k, Gemini 60k). (3) <i>smart_truncate</i> keeps the lead paragraphs plus the paragraphs densest in numbers, names and signal words, then restores original order, so semantics survive. (4) If a 413 (or a 400 \"context too long\") still occurs, the budget is halved and the same tier retried up to 3 times, then the next tier is tried. (5) For genuinely long documents the design is map-reduce: extract per chunk, then merge with grounding applied to each chunk.",
        "<b>429 / rate limits.</b> Requests wait locally instead of being rejected: a per-(provider, model) token bucket enforces RPM (implemented) and estimated TPM, shared via Redis in production so thousands of coroutines see one budget. On a 429 we honour <i>Retry-After</i>, else back off exponentially with full jitter (1 s base, 60 s cap). After repeated 429s the tier gets a cooldown (circuit breaker) and all workers skip it immediately, avoiding a retry storm.",
        "<b>Fallback chain.</b> Gemini main &gt; Gemini lite &gt; Groq (gpt-oss 120b / 20b), built from configured keys (a tier without a key is skipped; any OpenAI-compatible provider can be added as another tier). 5xx and malformed JSON get bounded retries; 401/403/404 disable that tier for the run. If every tier fails, the item is dead-lettered and nothing is made up.",
        "<b>Spend control.</b> A sha256 answer cache means retries and re-runs never pay twice; the products job also caps LLM calls with a budget flag."]),
    Paragraph("4. Freshness tracking: never process the same article or job twice across nodes", H2),
    *L(["<b>Two identities.</b> The canonical URL (lower-case host, tracking parameters and fragments stripped, trailing slash removed) and a content fingerprint (sha256 of normalized title + first 500 characters), so a syndicated copy under a new URL is also caught.",
        "<b>Atomic claim.</b> <i>claim(url, fingerprint)</i> inserts both keys with a single atomic statement and returns true only for the one caller that created them: Redis <i>SET key NX EX</i> or Postgres <i>INSERT ... ON CONFLICT DO NOTHING</i> across nodes; SQLite <i>INSERT OR IGNORE</i> on a primary key in the trial (same semantics, implemented and tested). In production the claim is taken <i>before</i> the expensive fetch/LLM step as a short lease that becomes permanent on success, so a crashed node does not lose the item; the trial claims just before writing a record.",
        "<b>Date normalization.</b> Priority: feed timestamp, then JSON-LD / meta tags / <i>&lt;time&gt;</i> on the page, then relative text (\"2 hours ago\", \"yesterday\"), then a first-seen heuristic for undated items, accepted once and flagged <i>date_confidence=heuristic</i>. All times are converted to UTC and checked against [now &minus; 24 h, now + 10 min]; future-dated items are rejected.",
        "<b>Cheap polling.</b> A per-source high-water mark (last published timestamp, ETag) lets each poll skip everything already seen."]),
    Paragraph("5. Storage strategy", H2),
    *L(["<b>Primary: PostgreSQL.</b> Entities have a stable core plus variable attributes (JSONB), need hard constraints (unique canonical IDs, foreign keys to the canonical entity) and transactional upserts for idempotent ingestion; <i>pg_trgm</i> supports fuzzy entity matching; partitioning by <i>collected_at</i> keeps tens of millions of rows manageable. Raw HTML/JSON is kept in object storage (S3) keyed by content hash, so extraction can be replayed without re-scraping.",
        "<b>Vector: pgvector first.</b> Embeddings of descriptions and abstracts (semantic dedup, search) live next to the data, with transactional consistency and one system to run. Move to a dedicated engine (e.g. Qdrant) only when vector count or filtered-ANN load outgrows Postgres.",
        "<b>Graph: edges in Postgres, graph engine as a read model.</b> Relationships (startup-product-founder-paper-repo-job-news) are stored as <i>entity_edges(src, rel, dst, evidence_url, observed_at)</i>, so every edge is traceable. Multi-hop queries (e.g. startups that hire ML engineers and publish papers with popular repos) run in Neo4j or Apache AGE, rebuilt from the edge table, which avoids dual-write inconsistency.",
        "<b>Dynamic metrics</b> (GitHub stars) go to an append-only <i>metric_observations(entity, metric, value, observed_at)</i>; the paper rows show the latest value and the time it was read. Redis holds dedup keys and rate-limit buckets."]),
    Paragraph("6. Anti-bot strategy for protected domains (Phase V)", H2),
    *L(["<b>Cheapest legitimate path first.</b> Official APIs, data dumps, RSS and sitemaps (all trial sources). Then polite HTTP: honest User-Agent with contact, robots.txt respected, per-host token bucket, conditional GET, jittered pacing. Most WAFs are never triggered at these rates.",
        "<b>Cloudflare / Datadome / JavaScript-rendered sites.</b> Use a TLS/HTTP2-fingerprint-consistent client (e.g. curl_cffi impersonation) or Playwright (async) with persistent browser contexts, realistic locale/timezone/viewport, and reuse of the clearance cookie for the session. Residential or ISP proxies with sticky sessions and low per-domain concurrency. The goal is to avoid captchas by behaving like a normal low-volume visitor, not to solve them.",
        "<b>When challenged.</b> Detect a challenge (403/503, challenge markers), back off with jitter, rotate the session, trip a per-domain breaker; commercial unblocking services only for a few high-value domains. No login/paywall circumvention.",
        "<b>Observed in the trial.</b> Article pages on economist.com, bloomberg.com and marktechpost.com returned 403. The pipeline degraded safely: it used the feed's own full text where the feed provides it and dropped articles with no readable text rather than storing empty or invented content. A browser-based fetcher is designed but not implemented in this trial."]),
]


def foot(c, d):
    c.saveState(); c.setFont("Helvetica", 7); c.setFillColor(MUTED)
    c.drawString(1.6 * cm, 0.9 * cm, "GraphOne Intelligence Pipeline - architecture"); c.drawRightString(A4[0] - 1.6 * cm, 0.9 * cm, f"{d.page}")
    c.restoreState()


SimpleDocTemplate("architecture.pdf", pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.4 * cm, bottomMargin=1.5 * cm,
                  title="GraphOne Intelligence Pipeline - Architecture", author="").build(story, onFirstPage=foot, onLaterPages=foot)
print("built architecture.pdf")
