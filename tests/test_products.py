import pytest
from src.pipelines.products import parse_show_hn, registered_domain, classify_pricing, build_record
from src.entity_resolver import EntityResolver


def hit(title, url="https://acme.io/app", oid="9"):
    return {"title": title, "url": url, "objectID": oid, "points": 10}


def test_parse_titles():
    assert parse_show_hn(hit("Show HN: Acme – AI notes for teams"))["name"] == "Acme"
    assert parse_show_hn(hit("Show HN: Acme: build faster"))["name"] == "Acme"
    assert parse_show_hn(hit("Show HN: Acme - cool thing"))["name"] == "Acme"
    assert parse_show_hn(hit("Show HN: I built a thing that does stuff for people")) is None   # no separator
    assert parse_show_hn(hit("Show HN: Acme – x", url="https://github.com/a/b")) is None
    assert parse_show_hn(hit("Show HN: Acme – x", url="https://someone.github.io/x")) is None
    assert parse_show_hn(hit("Ask HN: Acme – x")) is None
    assert parse_show_hn({"title": "Show HN: A – b", "url": None}) is None


def test_domains():
    assert registered_domain("https://www.app.acme.io/x") == "acme.io"
    assert registered_domain("https://foo.co.uk/") == "foo.co.uk"


@pytest.mark.parametrize("text,model", [
    ("Plans: Free plan $0. Pro $12/month per user. Enterprise: contact sales", "FREEMIUM"),
    ("Pro is $29/month. Team $99 per month", "PAID"),
    ("Contact sales for enterprise pricing and book a demo", "ENTERPRISE"),
    ("Pro is $29/month. Enterprise: contact sales", "PAID"),
    ("Usage $0.50 an order. Custom plans: book a demo", "PAID"),
    ("This tool is 100% free and always will be", "FREE"),
    ("Start for free today and explore", None),                       # weak signal alone: undecided
    ("We offer a free plan for individuals", "FREEMIUM"),
    ("Start for free. Pro is $12/month per user", "FREEMIUM"),        # a published price plus a free entry
    ("We make software. Read our blog.", None),
])
def test_pricing_rules(text, model):
    m, ev = classify_pricing(text)
    assert m == model
    if model:
        assert ev and ev.replace("  ", " ") in " ".join(text.split()) or ev in text   # evidence is a real quote


def test_build_record_startup_matching():
    R = EntityResolver(); R.register("CircuitHub")
    base = {"name": "Foo", "url": "https://foo.com", "domain": "foo.com", "hn_url": "https://news.ycombinator.com/item?id=1",
            "pricing": "PAID", "evidence": "$5/month", "points": 3}
    assert build_record(base, {"foo.com": "Foo Labs"}, R).content.startupMatch == "yc-domain"
    assert build_record({**base, "name": "OpenAI Inc"}, {}, R).content.startupName == "OpenAI"
    r = build_record({**base, "name": "Brandnew"}, {}, R)
    assert r.content.startupMatch == "maker-brand" and r.content.startupName == "Brandnew"
    assert build_record({**base, "pricing": None, "evidence": None}, {}, R).content.pricingModel is None


def test_evidence_trimmed_to_whole_words():
    text = "abcdefghij" * 12 + " Pro is $29/month for everyone " + "tail words here " * 12
    m, ev = classify_pricing(text)
    assert m == "PAID" and "$29/month" in ev
    assert not ev.startswith("abcdefghij")            # leading partial word was cut off
    assert ev == " ".join(ev.split())                  # whitespace normalized
    assert ev.split(" ")[-1] in text.split()           # ends on a whole word


def test_apply_yc_match():
    from src.pipelines.products import apply_yc_match
    c = {"productUrl": "https://www.app.foo.com/x", "startupName": "Foo", "startupMatch": "maker-brand"}
    assert apply_yc_match(c, {"foo.com": "Foo Labs"}) and c["startupName"] == "Foo Labs" and c["startupMatch"] == "yc-domain"
    assert not apply_yc_match(c, {"foo.com": "Foo Labs"})                    # already matched: unchanged
    assert not apply_yc_match({"productUrl": "https://bar.io", "startupMatch": "maker-brand"}, {"foo.com": "X"})
