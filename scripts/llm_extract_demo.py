"""Live check of the LLM engine on REAL pages, and an independent accuracy check of the rule-based pricing labels.
Takes a reproducible random sample of products, fetches each product's own site, asks the LLM chain for the pricing
model + a verbatim evidence quote (grounded: the quote must appear in the page), and compares with the rules.
    python scripts/llm_extract_demo.py --n 30        (uses ~n LLM calls; answers are cached)"""
import argparse, asyncio, json, random, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.http_client import AsyncFetcher
from src.io_utils import read_jsonl
from src.llm_orchestrator import ExtractionFailed, LLMOrchestrator
from src.logging_setup import setup_logging
from src.pipelines.products import LLM_INSTRUCTION, LLM_SCHEMA, fetch_page_text, registered_domain


def compare(rules: str, llm: str | None) -> str:
    if not llm:
        return "llm-undecided"
    return "agree" if rules == llm else "disagree"


async def main(n: int) -> None:
    rows = [r for r in read_jsonl("output/products.jsonl") if r["content"].get("pricingModel")]
    random.Random(7).shuffle(rows)
    rows = rows[:n]
    async with AsyncFetcher() as f, LLMOrchestrator(cache_path="data/llm_cache.sqlite") as llm:
        async def one(r):
            c = r["content"]
            text = await fetch_page_text(f, c["productUrl"], registered_domain(c["productUrl"]))
            base = {"product": c["productName"], "url": c["productUrl"], "rules": c["pricingModel"],
                    "rules_evidence": c.get("pricingEvidence")}
            if len(text) < 300:
                return {**base, "status": "page-unreadable"}
            try:
                res = await llm.extract(text=text, grounded_fields=["evidence"], instruction=LLM_INSTRUCTION,
                                        schema_hint=LLM_SCHEMA)
            except ExtractionFailed as e:
                return {**base, "status": "llm-failed", "error": str(e)[:300]}
            pm = (res.data.get("pricing_model") or "").upper() or None
            if pm and not res.data.get("evidence"):
                pm = None                                  # evidence was not found in the page: answer rejected
            return {**base, "status": compare(c["pricingModel"], pm), "llm": pm, "llm_evidence": res.data.get("evidence"),
                    "tier": res.tier, "truncated": res.truncated, "ungrounded": res.ungrounded}
        results = await asyncio.gather(*(one(r) for r in rows))
        stats = dict(llm.stats)
    Path("output").mkdir(exist_ok=True)
    Path("output/llm_demo.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in results), encoding="utf-8")
    st = Counter(x["status"] for x in results)
    decided = st["agree"] + st["disagree"]
    print(f"sampled {len(results)} products | status {dict(st)}")
    print(f"tiers used: {dict(Counter(x.get('tier') for x in results if x.get('tier')))}")
    print(f"orchestrator stats (ok:/fail:/429/413/cache_hit): {stats}")
    if decided:
        print(f"LLM vs rules agreement: {st['agree']}/{decided} = {100 * st['agree'] / decided:.0f}%")
    for x in results:
        if x["status"] == "disagree":
            print(f"  DISAGREE {x['product']}: rules={x['rules']} llm={x['llm']} | rules: {x['rules_evidence']!r} | llm: {x['llm_evidence']!r}")
    print("details: output/llm_demo.jsonl")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=30)
    setup_logging()
    asyncio.run(main(ap.parse_args().n))
