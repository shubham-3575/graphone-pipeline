"""Canonical record schemas (match the assignment's Expected Schemas).

Traceability rule: every record MUST carry a valid http(s) source URL, otherwise it is rejected.
This is our primary defence against hallucinated data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from src.config import SCHEMA_VERSION


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(BaseModel):
    name: str = Field(min_length=1)
    url: str

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("source.url must be an http(s) URL")
        return v


class PricingModel(str, Enum):
    FREE = "FREE"
    FREEMIUM = "FREEMIUM"
    PAID = "PAID"
    ENTERPRISE = "ENTERPRISE"


class _Base(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    source: Source
    collectedAt: datetime = Field(default_factory=utcnow)

    @field_validator("collectedAt")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


# ---- Startup ---------------------------------------------------------------
class StartupData(BaseModel):
    employeeCount: Optional[int] = Field(default=None, ge=0)


class StartupContent(BaseModel):
    entityName: str = Field(min_length=1)
    data: StartupData = Field(default_factory=StartupData)


class StartupRecord(_Base):
    recordType: Literal["STARTUP"] = "STARTUP"
    content: StartupContent


# ---- Product ---------------------------------------------------------------
class ProductContent(BaseModel):
    productName: Optional[str] = None  # extra field; useful to distinguish rows
    startupName: str = Field(min_length=1)
    pricingModel: Optional[PricingModel] = None
    productUrl: Optional[str] = None          # extra: the product's own website
    pricingEvidence: Optional[str] = None     # extra: verbatim quote from the page that justifies pricingModel
    startupMatch: Optional[str] = None        # extra: how startupName was derived (yc-domain | name-match | maker-brand)


class ProductRecord(_Base):
    recordType: Literal["PRODUCT"] = "PRODUCT"
    content: ProductContent


# ---- Research paper --------------------------------------------------------
class PaperContent(BaseModel):
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    paper_url: str
    github_url: Optional[str] = None
    github_stars: Optional[int] = Field(default=None, ge=0)
    published_date: datetime
    github_stars_fetched_at: Optional[datetime] = None  # freshness of the dynamic metric


class PaperRecord(_Base):
    recordType: Literal["RESEARCH_PAPER"] = "RESEARCH_PAPER"
    content: PaperContent


# ---- Job -------------------------------------------------------------------
class JobContent(BaseModel):
    title: Optional[str] = None
    company: str = Field(min_length=1)
    date: datetime
    is_remote: Optional[bool] = None
    role_family: Optional[str] = None
    job_url: Optional[str] = None
    ai_related: Optional[bool] = None         # extra: AI-company board, or AI/ML keyword in the title


class JobRecord(_Base):
    recordType: Literal["JOB"] = "JOB"
    content: JobContent


# ---- News (not specified in the doc; our own minimal schema) -------------------
class NewsContent(BaseModel):
    title: str = Field(min_length=1)
    published_date: datetime
    date_confidence: Literal["explicit", "relative", "heuristic"] = "explicit"
    full_text: str = ""
    article_url: str


class NewsRecord(_Base):
    recordType: Literal["NEWS"] = "NEWS"
    content: NewsContent
