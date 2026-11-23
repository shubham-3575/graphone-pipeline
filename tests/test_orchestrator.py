import pytest
from src.llm_orchestrator import (LLMOrchestrator, Tier, RateLimited, PayloadTooLarge, ProviderError,
                                  TierDisabled, ExtractionFailed, parse_json_loose, BadOutput)
from src.text_chunking import smart_truncate
from src.grounding import ground_fields

TEXT = "Acme AI raised funding. Acme AI has 120 employees. Pricing: freemium plan available."
KW = dict(text=TEXT, instruction="extract", schema_hint="{}", grounded_fields=["name", "employees"],
          required=["name"])


def tiers(n=3):
    return [Tier(f"t{i}", "openai", "m", "k", max_input_chars=4000) for i in range(n)]


async def nosleep(_): pass


def orch(script, ts=None, **kw):
    """script: dict tier_name -> list of outcomes (str result or Exception instance), consumed in order."""
    calls = []

    async def caller(tier, system, user):
        calls.append((tier.name, len(user)))
        o = script[tier.name].pop(0)
        if isinstance(o, Exception):
            raise o
        return o

    o = LLMOrchestrator(ts or tiers(), caller=caller, sleep=nosleep, **kw)
    o.calls = calls
    return o


GOOD = '{"name": "Acme AI", "employees": 120}'


async def test_success_first_tier():
    o = orch({"t0": [GOOD]})
    r = await o.extract(**KW)
    assert r.tier == "t0" and r.data["employees"] == 120


async def test_429_retries_then_succeeds_same_tier():
    o = orch({"t0": [RateLimited(1), RateLimited(None), GOOD]})
    r = await o.extract(**KW)
    assert r.tier == "t0" and o.stats["429"] == 2


async def test_429_exhausted_falls_back_and_sets_cooldown():
    o = orch({"t0": [RateLimited(1)] * 4, "t1": [GOOD, GOOD]})
    r = await o.extract(**KW)
    assert r.tier == "t1"
    r2 = await o.extract(**KW)           # t0 is cooling down -> straight to t1, no new t0 calls
    assert r2.tier == "t1"
    assert [c[0] for c in o.calls].count("t0") == 4


async def test_413_shrinks_payload_then_succeeds():
    big = "Acme AI has 120 employees. " + ("filler words here. " * 2000)
    o = orch({"t0": [PayloadTooLarge(), PayloadTooLarge(), GOOD]})
    r = await o.extract(**{**KW, "text": big})
    assert r.truncated and o.calls[2][1] < o.calls[0][1]


async def test_hallucinated_optional_field_is_nulled():
    o = orch({"t0": ['{"name": "Acme AI", "employees": 9999}']})
    r = await o.extract(**KW)
    assert r.data["employees"] is None and r.ungrounded == ["employees"]


async def test_hallucinated_required_field_rejected_all_tiers():
    bad = '{"name": "Totally Other Corp", "employees": 1}'
    o = orch({"t0": [bad], "t1": [bad], "t2": [bad]})
    with pytest.raises(ExtractionFailed):
        await o.extract(**KW)


async def test_auth_error_disables_tier():
    o = orch({"t0": [TierDisabled("401")], "t1": [GOOD, GOOD]})
    await o.extract(**KW); await o.extract(**KW)
    assert o.tiers[0].disabled and [c[0] for c in o.calls].count("t0") == 1


async def test_5xx_falls_through_to_next_tier():
    o = orch({"t0": [ProviderError("500")] * 3, "t1": [GOOD]})
    assert (await o.extract(**KW)).tier == "t1"


async def test_no_tiers():
    with pytest.raises(ExtractionFailed):
        await LLMOrchestrator([], caller=None).extract(**KW)


def test_json_loose():
    assert parse_json_loose('```json\n{"a":1}\n```') == {"a": 1}
    assert parse_json_loose('Sure! {"a": 2} hope it helps') == {"a": 2}
    with pytest.raises(BadOutput):
        parse_json_loose("nope")


def test_smart_truncate_budget_and_density():
    filler = "\n".join(["lorem ipsum dolor sit amet"] * 500)
    key = "Acme AI raised $40 million Series B and has 120 employees. Pricing: freemium."
    text = "Title line\n" + filler + "\n" + key + "\n" + filler
    out = smart_truncate(text, 1500)
    assert len(out) <= 1500 and "120 employees" in out and out.startswith("Title line")


def test_grounding_numbers_and_k_form():
    c, bad = ground_fields({"stars": 1200, "n": 5}, "repo has 1.2k stars", ["stars", "n"])
    assert c["stars"] == 1200 and bad == ["n"]


async def test_result_cache_avoids_second_call(tmp_path):
    calls = []

    async def caller(tier, system, user):
        calls.append(1); return GOOD

    o = LLMOrchestrator(tiers(1), caller=caller, sleep=nosleep, cache_path=str(tmp_path / "c.sqlite"))
    r1 = await o.extract(**KW)
    r2 = await o.extract(**KW)
    assert len(calls) == 1 and r2.tier == "cache" and r2.data == r1.data
    o2 = LLMOrchestrator(tiers(1), caller=caller, sleep=nosleep, cache_path=str(tmp_path / "c.sqlite"))
    await o2.extract(**KW)                       # a brand-new process also hits the cache
    assert len(calls) == 1


async def test_per_tier_throttle_spaces_calls():
    sleeps = []

    async def rec(d): sleeps.append(d)

    async def caller(tier, system, user): return GOOD

    t = [Tier("t0", "openai", "m", "k", max_input_chars=4000, rpm=60)]     # 1 call / second
    o = LLMOrchestrator(t, caller=caller, sleep=rec)
    for i in range(3):
        await o.extract(**{**KW, "text": TEXT + str(i)})
    assert len(sleeps) == 2 and 0.5 < sleeps[0] <= 1.0 and 1.5 < sleeps[1] <= 2.0
