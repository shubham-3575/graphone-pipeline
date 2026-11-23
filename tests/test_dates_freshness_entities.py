from datetime import datetime, timedelta, timezone
import pytest
from src.dates import parse_relative, parse_date_string, extract_published_date, is_fresh
from src.freshness import SeenStore, canonical_url
from src.entity_resolver import EntityResolver

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("txt,delta", [
    ("2 hours ago", timedelta(hours=2)), ("an hour ago", timedelta(hours=1)),
    ("35 minutes ago", timedelta(minutes=35)), ("3d ago", timedelta(days=3)),
    ("Posted 5 hrs ago", timedelta(hours=5)), ("yesterday", timedelta(days=1)),
    ("just now", timedelta(0)), ("1 week ago", timedelta(weeks=1))])
def test_relative(txt, delta):
    assert parse_relative(txt, NOW) == NOW - delta


def test_absolute_and_unix_and_tz():
    d, c = parse_date_string("2026-11-22T09:30:00+05:30", NOW)
    assert d == datetime(2026, 11, 22, 4, 0, tzinfo=timezone.utc) and c == "explicit"
    assert parse_date_string("1795336630", NOW)[0].tzinfo is not None
    assert parse_date_string("Sun, 22 Nov 2026 10:00:00 GMT", NOW)[0].hour == 10
    assert parse_date_string("garbage text", NOW)[0] is None


def test_extract_jsonld_meta_time_and_none():
    h1 = '<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-11-22T08:00:00Z"}</script>'
    assert extract_published_date(h1, NOW)[2] == "json-ld"
    h2 = '<meta property="article:published_time" content="2026-11-22T07:00:00+00:00">'
    d, c, m = extract_published_date(h2, NOW)
    assert d.hour == 7 and m.startswith("meta")
    h3 = '<div><time>3 hours ago</time></div>'
    d, c, m = extract_published_date(h3, NOW)
    assert d == NOW - timedelta(hours=3) and c == "relative"
    d, c, m = extract_published_date("<p>no date anywhere</p>", NOW)
    assert d is None and c == "heuristic"


def test_is_fresh_window():
    assert is_fresh(NOW - timedelta(hours=23, minutes=59), NOW)
    assert not is_fresh(NOW - timedelta(hours=24, minutes=1), NOW)
    assert not is_fresh(NOW + timedelta(hours=2), NOW)      # future dates rejected


def test_seen_store_dedup(tmp_path):
    s = SeenStore(str(tmp_path / "s.db"))
    assert s.claim("https://www.x.com/a/?utm_source=tw", "Big News", "body")
    assert not s.claim("https://x.com/a", "Big News", "body")               # same url, tracking stripped
    assert not s.claim("https://other.com/copy", "Big News", "body")        # syndicated copy, same fingerprint
    assert s.claim("https://x.com/b", "Different", "body2")
    assert canonical_url("HTTPS://WWW.X.com/a/#frag") == "https://x.com/a"


@pytest.fixture(scope="module")
def R():
    return EntityResolver("data/seed_startups.json")


@pytest.mark.parametrize("raw,canon", [
    ("OpenAI", "OpenAI"), ("OpenAI, Inc.", "OpenAI"), ("Open AI", "OpenAI"), ("openai", "OpenAI"),
    ("OPEN AI INC", "OpenAI"), ("Anthropic PBC", "Anthropic"), ("Hugging Face", "Hugging Face"),
    ("HuggingFace Inc.", "Hugging Face"), ("Mistral", "Mistral AI"), ("Weights and Biases", "Weights & Biases"),
    ("Character AI", "Character.AI"), ("Anysphere", "Cursor"), ("Stabilty AI", "Stability AI")])
def test_resolves(R, raw, canon):
    assert R.resolve(raw).canonical == canon


def test_unknown_not_forced(R):
    r = R.resolve("Totally Unknown Robotics Ltd")
    assert r.is_new_entity and r.method == "unresolved" and r.canonical == "Totally Unknown Robotics"


def test_log_rows(R):
    assert any(row["raw"] == "Open AI" and row["canonical"] == "OpenAI" for row in R.mapping_rows())
