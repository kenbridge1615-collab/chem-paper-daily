"""Scoring and selection (design section 6, requirements R2).

score = journal_weight + sum(matched keyword weights) + feedback_bonus
genre = electrochem if any electrochem keyword matches title/abstract else other

The daily genre is chosen by comparing the target ratio against the last
`balance_window_days` of actual introductions, picking whichever genre is most
under-represented. Within that genre the highest-scoring un-introduced paper
with an abstract wins; if that genre has no candidate, the other genre is used.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Config
from app.models import Paper

GENRE_ELECTROCHEM = "electrochem"
GENRE_OTHER = "other"


def classify_genre(paper: Paper, electrochem_keywords: list[str]) -> str:
    haystack = f"{paper.title or ''} {paper.abstract or ''}".lower()
    for kw in electrochem_keywords:
        if kw.lower() in haystack:
            return GENRE_ELECTROCHEM
    return GENRE_OTHER


def _journal_weight(paper: Paper, cfg: Config) -> int:
    issn = (paper.issn or "").strip()
    name = (paper.journal or "").strip().lower()
    for j in cfg.journals:
        if (issn and j.issn == issn) or (name and j.name.lower() == name):
            if j.tier == "top":
                return cfg.journal_weight_top
            if j.tier == "specialist":
                return cfg.journal_weight_specialist
            break
    return cfg.journal_weight_other


def _keyword_points(paper: Paper, cfg: Config) -> int:
    haystack = f"{paper.title or ''} {paper.abstract or ''}".lower()
    total = 0
    for kw, weight in cfg.keyword_weights.items():
        if kw.lower() in haystack:
            total += weight
    return total


def _feedback_bonus(
    paper: Paper,
    cfg: Config,
    feedback_journals: dict[str, int],
    feedback_keywords: dict[str, int],
) -> int:
    """+bonus for each thumbs-up signal matching this paper's journal or a
    keyword, -bonus for each thumbs-down signal. Signals are precomputed from
    past feedback (see build_feedback_signals)."""
    bonus = 0
    jkey = (paper.journal or "").strip().lower()
    if jkey and jkey in feedback_journals:
        bonus += cfg.feedback_bonus * feedback_journals[jkey]
    haystack = f"{paper.title or ''} {paper.abstract or ''}".lower()
    for kw, sign in feedback_keywords.items():
        if kw.lower() in haystack:
            bonus += cfg.feedback_bonus * sign
    return bonus


def build_feedback_signals(
    rated_papers: list[tuple[Paper, int]], cfg: Config
) -> tuple[dict[str, int], dict[str, int]]:
    """Aggregate feedback into per-journal and per-keyword sign sums.

    rated_papers is a list of (paper, rating) where rating is +1 or -1.
    """
    journals: dict[str, int] = {}
    keywords: dict[str, int] = {}
    for paper, rating in rated_papers:
        sign = 1 if rating > 0 else -1
        jkey = (paper.journal or "").strip().lower()
        if jkey:
            journals[jkey] = journals.get(jkey, 0) + sign
        haystack = f"{paper.title or ''} {paper.abstract or ''}".lower()
        for kw in cfg.keyword_weights:
            if kw.lower() in haystack:
                keywords[kw] = keywords.get(kw, 0) + sign
    return journals, keywords


def score_paper(
    paper: Paper,
    cfg: Config,
    feedback_journals: dict[str, int] | None = None,
    feedback_keywords: dict[str, int] | None = None,
) -> int:
    return (
        _journal_weight(paper, cfg)
        + _keyword_points(paper, cfg)
        + _feedback_bonus(
            paper, cfg, feedback_journals or {}, feedback_keywords or {}
        )
    )


@dataclass
class ScoredPaper:
    paper: Paper
    score: int
    genre: str


def choose_daily_genre(
    cfg: Config, recent_genres: list[str]
) -> str:
    """Pick the genre that is most under its target share over the recent window.

    With an empty history, fall back to the larger target share (other).
    """
    total = len(recent_genres)
    if total == 0:
        return GENRE_ELECTROCHEM if cfg.genre_electrochem >= cfg.genre_other else GENRE_OTHER

    actual_electro = sum(1 for g in recent_genres if g == GENRE_ELECTROCHEM) / total
    actual_other = 1.0 - actual_electro
    # Deficit = target - actual. Larger deficit means more under-represented.
    deficit_electro = cfg.genre_electrochem - actual_electro
    deficit_other = cfg.genre_other - actual_other
    return GENRE_ELECTROCHEM if deficit_electro >= deficit_other else GENRE_OTHER


def select_paper(
    candidates: list[Paper],
    cfg: Config,
    *,
    recent_genres: list[str],
    feedback_journals: dict[str, int] | None = None,
    feedback_keywords: dict[str, int] | None = None,
) -> ScoredPaper | None:
    """Select one paper to introduce.

    Candidates are expected to already be un-introduced and have an abstract
    (storage.candidate_papers enforces this). Returns None if there is nothing
    to pick.
    """
    if not candidates:
        return None

    scored: list[ScoredPaper] = []
    for p in candidates:
        genre = classify_genre(p, cfg.electrochem_keywords)
        s = score_paper(p, cfg, feedback_journals, feedback_keywords)
        scored.append(ScoredPaper(paper=p, score=s, genre=genre))

    target_genre = choose_daily_genre(cfg, recent_genres)
    primary = [sp for sp in scored if sp.genre == target_genre]
    pool = primary if primary else scored

    # Highest score wins; ties broken by most recent publication date.
    pool.sort(
        key=lambda sp: (sp.score, sp.paper.published_date or ""),
        reverse=True,
    )
    best = pool[0]
    best.paper.genre = best.genre
    return best
