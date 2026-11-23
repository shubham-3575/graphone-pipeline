from datetime import datetime, timedelta, timezone
from src.common import role_family
from src.entity_resolver import EntityResolver
from src.freshness import SeenStore
from src.pipelines import jobs, news

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
ago = lambda h: str(int((NOW - timedelta(hours=h)).timestamp()))


def test_role_family():
    assert role_family("Senior Machine Learning Engineer") == "Data & ML"
    assert role_family("Backend Engineer (Go)") == "Engineering"
    assert role_family("Product Designer") == "Design"
    assert role_family("Account Executive") == "Sales"
    assert role_family("Underwater Basket Weaver") == "Other"


def test_adapters():
    r = jobs.parse_remoteok([{"legal": "x"}, {"position": "ML Engineer", "company": "Acme", "epoch": 1, "url": "https://r/1"}])
    assert len(r) == 1 and r[0]["remote"] is True
    a = jobs.parse_arbeitnow({"data": [{"title": "Dev", "company_name": "B", "created_at": 5, "url": "https://a/1", "remote": False}]})
    assert a[0]["remote"] is False
    w = jobs.parse_wwr("<rss><channel><item><title>Acme: Data Scientist</title><link>https://w/1</link>"
                       "<pubDate>Sun, 22 Nov 2026 10:00:00 +0000</pubDate></item></channel></rss>")
    assert w[0]["company"] == "Acme" and w[0]["title"] == "Data Scientist"


def test_to_record_fresh_stale_dup(tmp_path):
    R, S = EntityResolver(), SeenStore(str(tmp_path / "s.db"))
    base = dict(source="RemoteOK", title="ML Engineer", company="OpenAI, Inc.", url="https://r/1", remote=True)
    rec, why = jobs.to_record({**base, "date_raw": ago(3)}, NOW, R, S)
    assert why == "ok" and rec.content.company == "OpenAI" and rec.content.role_family == "Data & ML"
    assert jobs.to_record({**base, "date_raw": ago(3)}, NOW, R, S)[1] == "duplicate"
    assert jobs.to_record({**base, "url": "https://r/2", "title": "Other", "date_raw": ago(30)}, NOW, R, S)[1] == "stale"
    assert jobs.to_record({**base, "url": "https://r/3", "title": "T3", "date_raw": ""}, NOW, R, S)[1] == "no-date"
    assert jobs.to_record({**base, "url": "https://r/4", "title": "Sales Lead", "date_raw": ago(1)}, NOW, R, S, ai_only=True)[1] == "not-ai"


FEED = """<rss version="2.0"><channel>
<item><title>Fresh &amp; Hot</title><link>https://n.com/a</link><pubDate>Sun, 22 Nov 2026 09:00:00 +0000</pubDate>
<description>&lt;p&gt;Summary text&lt;/p&gt;</description></item>
<item><title>Old</title><link>https://n.com/b</link><pubDate>Fri, 20 Nov 2026 09:00:00 +0000</pubDate></item>
<item><title>Undated</title><link>https://n.com/c</link></item></channel></rss>"""


def test_news_feed_parse_and_freshness():
    items = news.parse_feed("X", FEED)
    assert [i["title"] for i in items] == ["Fresh & Hot", "Old", "Undated"]
    assert items[0]["feed_text"] == "Summary text" and items[2]["date"] is None
    from src.dates import is_fresh
    assert is_fresh(items[0]["date"], NOW) and not is_fresh(items[1]["date"], NOW)


def test_hn_parse_skips_text_posts():
    d = {"hits": [{"title": "T", "url": "https://example.org/a", "created_at_i": 1795300000, "objectID": "1"},
                  {"title": "Ask HN", "url": None, "created_at_i": 1795300000, "objectID": "2"}]}
    assert len(news.parse_hn(d)) == 1


def test_full_text_rule():
    assert not news.has_full_text("") and not news.has_full_text("short " * 10)
    assert news.has_full_text("word " * 100)


def test_hn_social_hosts_filtered_by_domain_not_substring():
    d = {"hits": [{"title": "A", "url": "https://twitter.com/a/status/1", "created_at_i": 1795300000, "objectID": "1"},
                  {"title": "B", "url": "https://www.box.com/blog/ai", "created_at_i": 1795300000, "objectID": "2"},
                  {"title": "C", "url": "https://x.com/p", "created_at_i": 1795300000, "objectID": "3"}]}
    assert [i["title"] for i in news.parse_hn(d)] == ["B"]


def test_board_stats_and_himalayas():
    ds = jobs.board_stats([{"date_raw": ago(2)}, {"date_raw": ago(50)}, {"date_raw": ""}], NOW)
    assert ds == {"listed": 3, "dated": 2, "fresh": 1, "newest_age_h": 2.0}
    h = jobs.parse_himalayas({"jobs": [{"title": "ML Eng", "companyName": "Acme", "pubDate": 1795300000,
                                        "applicationLink": "https://h/1"}]})
    assert h[0]["company"] == "Acme" and h[0]["url"] == "https://h/1"


def test_ashby_parser_and_ai_flag(tmp_path):
    a = jobs.parse_ashby({"jobs": [{"title": "Office Manager", "isRemote": False, "publishedAt": "2026-11-22T10:00:00.000+00:00",
                                    "jobUrl": "https://jobs.ashbyhq.com/openai/abc"}]}, "OpenAI")
    assert a[0]["company"] == "OpenAI" and a[0]["remote"] is False and a[0]["ai_board"] is True
    assert jobs.parse_ashby("garbage", "X") == []
    R, S = EntityResolver(), SeenStore(str(tmp_path / "s.db"))
    rec, why = jobs.to_record(a[0], NOW, R, S)
    assert why == "ok" and rec.content.ai_related is True            # AI-company board: flagged even without a keyword
    g = dict(source="Jobicy", title="Sales Lead", company="Acme", url="https://j/9", date_raw=ago(1), remote=True)
    assert jobs.to_record(g, NOW, R, S)[0].content.ai_related is False
    g2 = {**g, "url": "https://j/10", "title": "Machine Learning Engineer"}
    assert jobs.to_record(g2, NOW, R, S)[0].content.ai_related is True
    g3 = {**g, "url": "https://j/11", "title": "Sales Rep"}
    assert jobs.to_record(g3, NOW, R, S, ai_only=True)[1] == "not-ai"
