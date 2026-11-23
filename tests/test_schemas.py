import pytest
from pydantic import ValidationError
from src.schemas import StartupRecord, ProductRecord, JobRecord


def test_startup_ok():
    r = StartupRecord(source={"name": "x", "url": "https://x.com/a"},
                      content={"entityName": "OpenAI", "data": {"employeeCount": 700}})
    assert r.recordType == "STARTUP" and r.schemaVersion == "1.0"
    assert r.collectedAt.tzinfo is not None


def test_source_url_required_and_http():
    with pytest.raises(ValidationError):
        StartupRecord(source={"name": "x", "url": "not-a-url"}, content={"entityName": "A"})


def test_pricing_enum():
    with pytest.raises(ValidationError):
        ProductRecord(source={"name": "x", "url": "https://x.com"},
                      content={"startupName": "A", "pricingModel": "CHEAP"})


def test_job_requires_date():
    with pytest.raises(ValidationError):
        JobRecord(source={"name": "x", "url": "https://x.com"}, content={"company": "A"})
