from src.pipelines.startups import select, to_record, is_ai_company, team_size
from src.entity_resolver import EntityResolver

DATA = [
 {"id": 1, "name": "Acme AI", "slug": "acme-ai", "one_liner": "AI agents for sales", "team_size": 12,
  "launched_at": 300, "status": "Active", "url": "https://www.ycombinator.com/companies/acme-ai"},
 {"id": 2, "name": "PlainCo", "slug": "plainco", "one_liner": "Payroll software", "team_size": 5, "launched_at": 200},
 {"id": 3, "name": "Ghost", "slug": "ghost", "one_liner": "LLM tooling", "team_size": 0, "launched_at": 100,
  "status": "Inactive"},
 {"id": 4, "name": "Beta", "slug": "beta", "tags": ["Machine Learning"], "team_size": None, "launched_at": 50},
 {"id": 5, "name": "Acme AI", "slug": "acme-ai", "one_liner": "dup AI", "launched_at": 10},
 {"id": 6, "name": "", "slug": "x", "one_liner": "AI"},
]


def test_filter_dedupe_inactive():
    names = [c["name"] for c in select(DATA, 10)]
    assert names == ["Acme AI", "Beta"]            # non-AI, inactive, duplicate, nameless all excluded
    assert [c["name"] for c in select(DATA, 10, ai_only=False)] == ["Acme AI", "PlainCo", "Beta"]


def test_record_mapping():
    r = to_record(DATA[0])
    assert r.content.entityName == "Acme AI" and r.content.data.employeeCount == 12
    assert r.source.url == "https://www.ycombinator.com/companies/acme-ai" and r.recordType == "STARTUP"
    assert to_record(DATA[3]).content.data.employeeCount is None
    assert team_size({"team_size": "0"}) is None and team_size({"team_size": "7"}) == 7
    assert to_record(DATA[3]).source.url.endswith("/companies/beta")      # built from slug


def test_ai_detector():
    assert is_ai_company({"one_liner": "Generative video"}) and not is_ai_company({"one_liner": "Payroll"})


def test_register_runtime_entities():
    R = EntityResolver()
    R.register("CircuitHub", ["Circuit Hub Inc"])
    assert R.resolve("Circuit Hub").canonical == "CircuitHub"
    assert R.resolve("OpenAI, Inc.").canonical == "OpenAI"          # seed still wins


def test_unique_records_drops_duplicate_names_and_refills():
    from src.pipelines.startups import unique_records
    R = EntityResolver()
    chosen = [{"name": "Foo", "slug": "foo"}, {"name": "foo", "slug": "foo-2"}, {"name": "Open AI", "slug": "oa"},
              {"name": "OpenAI, Inc.", "slug": "oa2"}, {"name": "Bar", "slug": "bar"}, {"name": "Baz", "slug": "baz"}]
    names = [r.content.entityName for r in unique_records(chosen, R, 4)]
    assert names == ["Foo", "OpenAI", "Bar", "Baz"]          # dup "foo", dup OpenAI skipped; list refilled to 4
