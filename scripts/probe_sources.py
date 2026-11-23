"""Run from the project root:  python scripts/probe_sources.py
Checks which candidate data sources respond from YOUR network and which LLM model names your keys can use.
Prints only statuses and short samples. API keys are never printed."""
import asyncio, os, sys, json
sys.path.insert(0, os.getcwd())
import aiohttp
from src import config

UA = {"User-Agent": config.USER_AGENT or "GraphOneResearchBot/1.0"}
GH = {"Authorization": f"Bearer {config.GITHUB_TOKEN}"} if config.GITHUB_TOKEN else {}

SOURCES = {
 # --- papers ---
 "arxiv API":            "https://export.arxiv.org/api/query?search_query=cat:cs.LG&max_results=2&sortBy=submittedDate",
 "HF daily_papers":      "https://huggingface.co/api/daily_papers?limit=2",
 "HF paper by id":       "https://huggingface.co/api/papers/2401.04088",
 "SemanticScholar":      "https://api.semanticscholar.org/graph/v1/paper/arXiv:2401.04088?fields=title,authors,externalIds",
 "GitHub repo (stars)":  "https://api.github.com/repos/huggingface/transformers",
 "paperswithcode.co":    "https://paperswithcode.co/paper/98456",
 # --- startups / products ---
 "YC companies (oss)":   "https://yc-oss.github.io/api/companies/all.json",
 "HN Show HN (algolia)": "https://hn.algolia.com/api/v1/search_by_date?tags=show_hn&hitsPerPage=2",
 "ProductHunt feed":     "https://www.producthunt.com/feed",
 # --- news (RSS) ---
 "news: TechCrunch AI":  "https://techcrunch.com/category/artificial-intelligence/feed/",
 "news: VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
 "news: The Verge AI":   "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
 "news: MIT TechReview": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
 "news: HN AI (algolia)":"https://hn.algolia.com/api/v1/search_by_date?query=AI&tags=story&hitsPerPage=2",
 "news: arXiv cs.AI rss":"https://rss.arxiv.org/rss/cs.AI",
 # --- jobs ---
 "jobs: RemoteOK":       "https://remoteok.com/api",
 "jobs: Remotive":       "https://remotive.com/api/remote-jobs?search=machine%20learning&limit=2",
 "jobs: WeWorkRemotely": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
 "jobs: Arbeitnow":      "https://www.arbeitnow.com/api/job-board-api",
 "jobs: Jobicy":         "https://jobicy.com/api/v2/remote-jobs?count=2&tag=ai",
 "jobs: Himalayas":      "https://himalayas.app/jobs/api?limit=2",
}

async def probe(s, name, url):
    h = {**UA, **(GH if "api.github.com" in url else {})}
    try:
        async with s.get(url, headers=h, timeout=aiohttp.ClientTimeout(total=25)) as r:
            body = (await r.text())[:160].replace("\n", " ")
            return f"{'OK ' if r.status==200 else 'BAD'} {r.status:<4} {name:<22} {r.headers.get('Content-Type','')[:28]:<28} {body}"
    except Exception as e:
        return f"ERR     {name:<22} {type(e).__name__}: {str(e)[:80]}"

async def models(s):
    out = []
    if config.GEMINI_API_KEY:
        try:
            async with s.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                             headers={"x-goog-api-key": config.GEMINI_API_KEY}) as r:
                if r.status == 200:
                    j = await r.json()
                    names = [m["name"].split("/")[-1] for m in j.get("models", [])
                             if "generateContent" in m.get("supportedGenerationMethods", [])]
                    out.append("GEMINI models: " + ", ".join(n for n in names if "flash" in n)[:900])
                else:
                    out.append(f"GEMINI list failed: HTTP {r.status}")
        except Exception as e:
            out.append(f"GEMINI error {e}")
    else:
        out.append("GEMINI: no key in .env")
    if config.GROQ_API_KEY:
        try:
            async with s.get("https://api.groq.com/openai/v1/models",
                             headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"}) as r:
                if r.status == 200:
                    j = await r.json()
                    out.append("GROQ models: " + ", ".join(sorted(m["id"] for m in j["data"]))[:900])
                else:
                    out.append(f"GROQ list failed: HTTP {r.status}")
        except Exception as e:
            out.append(f"GROQ error {e}")
    else:
        out.append("GROQ: no key in .env")
    out.append(f"GITHUB token present: {bool(config.GITHUB_TOKEN)}")
    return out

async def main():
    async with aiohttp.ClientSession() as s:
        res = await asyncio.gather(*(probe(s, n, u) for n, u in SOURCES.items()))
        print("\n".join(res)); print()
        print("\n".join(await models(s)))

if __name__ == "__main__":
    asyncio.run(main())
