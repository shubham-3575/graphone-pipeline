"""Labeled evaluation of the entity resolver (seed list only, no YC names registered, fully deterministic).
Positive cases: messy variants that MUST resolve to the expected canonical name.
Negative cases: look-alike names that must NOT be merged (they must stay unresolved).
Writes output/entity_eval.jsonl (appended to the 'Entity Mapping Log' tab, origin = synthetic-eval).
    python scripts/resolver_eval.py"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.entity_resolver import EntityResolver

POS = {
 "OpenAI": ["OpenAI", "OpenAI, Inc.", "Open AI", "open ai", "OPENAI", "OpenAI Inc", "OpenAI LP", "Open-AI", "OpenAI Global, LLC"],
 "Anthropic": ["Anthropic", "Anthropic PBC", "anthropic", "Anthropic, PBC", "Anthropic AI"],
 "Hugging Face": ["HuggingFace", "Hugging Face, Inc.", "hugging-face", "Huggingface Inc", "HUGGING FACE"],
 "Mistral AI": ["Mistral", "MistralAI", "Mistral AI SAS", "mistral ai"],
 "Stability AI": ["StabilityAI", "Stability.ai", "Stability AI Ltd", "Stabilty AI"],
 "Weights & Biases": ["Weights and Biases", "W&B", "wandb", "Weights & Biases Inc"],
 "Character.AI": ["Character AI", "CharacterAI", "character.ai"],
 "Cursor": ["Anysphere", "Anysphere Inc", "Cursor AI"],
 "Perplexity AI": ["Perplexity", "Perplexity.ai", "perplexity ai"],
 "Scale AI": ["ScaleAI", "Scale AI, Inc.", "scale ai"],
 "Together AI": ["TogetherAI", "Together Computer"],
 "Cohere": ["Cohere Inc.", "Cohere AI", "cohere"],
 "ElevenLabs": ["Eleven Labs", "elevenlabs", "Elevenlabs.io"],
 "xAI": ["x.AI", "X AI", "xAI Corp"],
 "DeepSeek": ["Deep Seek", "DeepSeek AI", "deepseek"],
 "Databricks": ["Databricks, Inc.", "databricks"],
}
NEG = ["Cohere Health", "Jasper Health", "Scale Computing", "Lambda School", "Harvey Nash", "Open AI Labs",
       "Hugging Face Hub", "Runway Financial", "Mistral Systems", "Anthropic Capital Partners", "Cerebras Wealth",
       "OpenAI Foundation Trust Fund", "Perplexity Games", "Groq Capital", "Together Bank"]


def main() -> int:
    R = EntityResolver()
    rows, ok, wrong, miss = [], 0, [], []
    for canon, variants in POS.items():
        for raw in variants:
            r = R.resolve(raw)
            good = r.canonical == canon
            ok += good
            if not good:
                (wrong if r.method != "unresolved" else miss).append((raw, canon, r.canonical, r.method))
            rows.append({**r.as_row(), "origin": "synthetic-eval", "expected": canon, "correct": good})
    neg_ok = 0
    for raw in NEG:
        r = R.resolve(raw)
        good = r.method == "unresolved"
        neg_ok += good
        if not good:
            wrong.append((raw, "(none)", r.canonical, r.method))
        rows.append({**r.as_row(), "origin": "synthetic-eval", "expected": "(unresolved)", "correct": good})
    npos = sum(len(v) for v in POS.values())
    resolved_total = sum(1 for r in rows if r["method"] != "unresolved")
    correct_resolved = sum(1 for r in rows if r["method"] != "unresolved" and r["correct"])
    print(f"positive cases : {ok}/{npos} resolved to the right canonical ({100*ok/npos:.1f}% recall)")
    print(f"negative cases : {neg_ok}/{len(NEG)} correctly left unmerged")
    print(f"precision      : {correct_resolved}/{resolved_total} merges were correct ({100*correct_resolved/resolved_total:.1f}%)")
    for w in wrong: print("  WRONG MERGE:", w)
    for m in miss: print("  MISSED     :", m)
    Path("output").mkdir(exist_ok=True)
    Path("output/entity_eval.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
