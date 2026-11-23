from src.pipelines.papers import parse_arxiv_feed, extract_github_repo, arxiv_url

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
 <entry>
  <id>http://arxiv.org/abs/2411.00001v2</id>
  <published>2026-11-20T17:59:59Z</published>
  <title>A Test
   Paper on Agents</title>
  <summary>We release code at https://github.com/foo/bar-baz. Enjoy.</summary>
  <author><name>Ada Lovelace</name></author><author><name>Alan Turing</name></author>
  <arxiv:comment>12 pages</arxiv:comment>
  <link href="http://arxiv.org/abs/2411.00001v2" rel="alternate" type="text/html"/>
 </entry>
 <entry>
  <id>http://arxiv.org/abs/2411.00002v1</id>
  <published>2026-11-19T10:00:00Z</published>
  <title>No Code Paper</title><summary>Nothing here.</summary>
  <author><name>Bob</name></author>
 </entry>
 <entry>
  <id>http://arxiv.org/abs/2411.00003v1</id>
  <published>2026-11-18T10:00:00Z</published>
  <title>Comment Link</title><summary>x</summary><author><name>C</name></author>
  <arxiv:comment>Code: github.com/orgs/x and https://github.com/real/repo.git</arxiv:comment>
 </entry>
</feed>"""


def test_parse_feed():
    items = parse_arxiv_feed(FEED)
    assert len(items) == 3
    a = items[0]
    assert a["arxiv_id"] == "2411.00001" and a["paper_url"] == "https://arxiv.org/abs/2411.00001"
    assert a["title"] == "A Test Paper on Agents" and a["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert a["published"].year == 2026 and a["published"].tzinfo is not None
    assert a["repo"] == ("foo", "bar-baz")
    assert items[1]["repo"] is None
    assert items[2]["repo"] == ("real", "repo")


def test_repo_extraction_edge_cases():
    assert extract_github_repo("see (https://github.com/a/b).") == ("a", "b")
    assert extract_github_repo("https://github.com/sponsors/x") is None
    assert extract_github_repo("nothing") is None
    assert extract_github_repo("https://github.com/a/b/tree/main") == ("a", "b")


def test_url_builder():
    u = arxiv_url(200, 100)
    assert "start=200" in u and "max_results=100" in u and "all%3Agithub" in u


def test_plain_mode_url_has_no_github_term():
    assert "github" not in arxiv_url(0, 100, "plain")
