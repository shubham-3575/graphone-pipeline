import json
from src import export_sheets as ex
from src.schemas import PaperRecord, PaperContent, Source, StartupRecord, StartupContent, JobRecord, JobContent
from datetime import datetime, timezone


def test_tables_and_csv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    now = datetime(2026, 11, 22, tzinfo=timezone.utc)
    p = PaperRecord(source=Source(name="arXiv", url="https://arxiv.org/abs/1"),
                    content=PaperContent(title="T", authors=["A", "B"], paper_url="https://arxiv.org/abs/1",
                                         github_url="https://github.com/a/b", github_stars=5, published_date=now))
    s = StartupRecord(source=Source(name="YC", url="https://x.com"), content=StartupContent(entityName="Acme"))
    j = JobRecord(source=Source(name="J", url="https://j.com"), content=JobContent(company="Acme", date=now))
    for name, rec in [("papers", p), ("startups", s), ("jobs", j)]:
        (tmp_path / "output" / f"{name}.jsonl").write_text(rec.model_dump_json() + "\n")
    (tmp_path / "output" / "entity_mapping.jsonl").write_text(json.dumps(
        {"raw": "Open AI", "canonical": "OpenAI", "method": "squashed", "score": 98.0, "is_new_entity": False}) + "\n")
    t = ex.build_tables()
    assert t["Research Papers"][1][1] == "A; B" and t["Research Papers"][1][4] == 5
    assert len(t["Startups"]) == 2 and len(t["Products"]) == 1 and len(t["News"]) == 1   # header only
    assert t["Entity Mapping Log"][1][:2] == ["Open AI", "OpenAI"]
    ex.write_csv(t)
    assert (tmp_path / "output" / "csv" / "Research_Papers.csv").exists()


def test_mapping_log_drops_self_maps_and_sorts_resolved_first(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    row = lambda raw, canon, m, **k: json.dumps({"raw": raw, "canonical": canon, "method": m, "score": 1.0,
                                                  "is_new_entity": m == "unresolved", **k}) + "\n"
    (tmp_path / "output" / "entity_mapping.jsonl").write_text(
        row("Zed", "Zed", "unresolved") + row("Foo Inc.", "Foo", "unresolved") + row("Open AI", "OpenAI", "squashed"))
    (tmp_path / "output" / "entity_eval.jsonl").write_text(
        row("Cohere Health", "Cohere Health", "unresolved", origin="synthetic-eval", expected="(unresolved)", correct=True))
    t = ex.build_tables()["Entity Mapping Log"]
    raws = [r[0] for r in t[1:]]
    assert raws[0] == "Open AI" and "Zed" not in raws and set(raws) == {"Open AI", "Foo Inc.", "Cohere Health"}
    assert t[0][-3:] == ["origin", "expected", "correct"]


def test_iso_format_is_uniform_and_full_text_single_line():
    assert ex._iso("2026-11-22T17:08:09.743000Z") == "2026-11-22T17:08:09Z"
    assert ex._iso("2026-11-23T08:30:11+05:30") == "2026-11-23T03:00:11Z"
    assert ex._iso("2026-11-23T08:30:11") == "2026-11-23T08:30:11Z"
    assert ex._iso(None) is None and ex._iso("not a date") == "not a date"
    news = ex.TABS["News"][2]({"recordType": "NEWS", "schemaVersion": "1.0", "collectedAt": "2026-11-23T03:00:00Z",
                               "source": {"name": "X", "url": "https://x"},
                               "content": {"title": "T", "published_date": "2026-11-23T01:00:00Z", "article_url": "https://x/a",
                                           "full_text": "line one\n\n  line two\tend"}})
    assert news[4] == "line one line two end"
