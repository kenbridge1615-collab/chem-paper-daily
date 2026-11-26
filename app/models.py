"""Shared data structures used across collection, selection, and generation."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Paper:
    """A paper's metadata as collected from a source.

    `id` is set once the paper is stored. `genre` is assigned during selection.
    """

    doi: str | None
    title: str
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    issn: str | None = None
    published_date: str | None = None  # ISO date string YYYY-MM-DD
    abstract: str | None = None
    source: str | None = None  # crossref | chemrxiv | pubmed | manual
    url: str | None = None
    oa_pdf_url: str | None = None
    genre: str | None = None  # electrochem | other
    id: int | None = None

    def has_abstract(self) -> bool:
        return bool(self.abstract and self.abstract.strip())
