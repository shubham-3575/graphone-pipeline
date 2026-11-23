"""Re-apply the YC-domain match to an existing output/products.jsonl (no page fetching, takes seconds).
    python scripts/rematch_products.py"""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.http_client import AsyncFetcher
from src.pipelines.products import apply_yc_match, load_yc_domains


async def main() -> None:
    async with AsyncFetcher() as f:
        yc = await load_yc_domains(f)
    path = Path("output/products.jsonl")
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    changed = sum(apply_yc_match(r["content"], yc) for r in rows)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    from collections import Counter
    print(f"YC domains known: {len(yc)} | products re-matched: {changed} | now: "
          f"{dict(Counter(r['content'].get('startupMatch') for r in rows))}")


asyncio.run(main())
