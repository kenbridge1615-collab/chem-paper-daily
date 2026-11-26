"""Unit tests for scoring, genre classification, and allocation (task 3.3)."""

from __future__ import annotations

from app.models import Paper
from app.select import (
    GENRE_ELECTROCHEM,
    GENRE_OTHER,
    build_feedback_signals,
    choose_daily_genre,
    classify_genre,
    score_paper,
    select_paper,
)


def _paper(**kw) -> Paper:
    base = dict(doi="10.1/x", title="t", abstract="a")
    base.update(kw)
    return Paper(**base)


def test_classify_genre(cfg):
    electro = _paper(title="A new ion-selective electrode", abstract="potentiometric")
    other = _paper(title="Total synthesis of a natural product", abstract="catalysis")
    assert classify_genre(electro, cfg.electrochem_keywords) == GENRE_ELECTROCHEM
    assert classify_genre(other, cfg.electrochem_keywords) == GENRE_OTHER


def test_journal_weight_in_score(cfg):
    top = _paper(journal="Journal of the American Chemical Society", issn="0002-7863",
                 title="x", abstract="y")
    specialist = _paper(journal="ACS Sensors", issn="2379-3694", title="x", abstract="y")
    unknown = _paper(journal="Some Other Journal", issn="9999-9999", title="x", abstract="y")
    assert score_paper(top, cfg) == 3
    assert score_paper(specialist, cfg) == 2
    assert score_paper(unknown, cfg) == 1


def test_keyword_points_accumulate(cfg):
    p = _paper(journal="Some Other Journal", issn="9999-9999",
               title="ionophore based ion-selective electrode",
               abstract="potentiometric pH sensor")
    # other(1) + ionophore(2) + ion-selective(2) + electrode(1) + potentiometric(2)
    # + pH sensor(2) + sensor(1) = 11
    assert score_paper(p, cfg) == 11


def test_feedback_bonus(cfg):
    liked = _paper(journal="ACS Sensors", title="ionophore work", abstract="")
    signals_j, signals_k = build_feedback_signals([(liked, 1)], cfg)
    candidate = _paper(journal="ACS Sensors", issn="2379-3694",
                       title="new ionophore", abstract="")
    with_fb = score_paper(candidate, cfg, signals_j, signals_k)
    without_fb = score_paper(candidate, cfg)
    assert with_fb > without_fb


def test_choose_daily_genre_empty_history(cfg):
    # No history -> fall back to the larger target share (other, 0.6).
    assert choose_daily_genre(cfg, []) == GENRE_OTHER


def test_choose_daily_genre_corrects_bias(cfg):
    # Recent history is all electrochem -> next should pick other.
    assert choose_daily_genre(cfg, [GENRE_ELECTROCHEM] * 10) == GENRE_OTHER
    # Recent history all other -> electrochem is under target.
    assert choose_daily_genre(cfg, [GENRE_OTHER] * 10) == GENRE_ELECTROCHEM


def test_select_prefers_target_genre_then_score(cfg):
    electro_low = _paper(doi="10.1/e", journal="ChemElectroChem", issn="2196-0216",
                         title="electrode study", abstract="voltammetry")
    other_high = _paper(doi="10.1/o", journal="Journal of the American Chemical Society",
                        issn="0002-7863", title="synthesis", abstract="catalysis")
    # History all electrochem -> target becomes other, so other_high is picked.
    chosen = select_paper([electro_low, other_high], cfg,
                          recent_genres=[GENRE_ELECTROCHEM] * 10)
    assert chosen is not None
    assert chosen.paper.doi == "10.1/o"
    assert chosen.genre == GENRE_OTHER


def test_select_falls_back_when_target_genre_empty(cfg):
    only_other = _paper(doi="10.1/o", journal="JACS", title="synthesis", abstract="catalysis")
    # Target electrochem but no electrochem candidate -> fall back to other.
    chosen = select_paper([only_other], cfg, recent_genres=[GENRE_OTHER] * 10)
    assert chosen is not None
    assert chosen.paper.doi == "10.1/o"


def test_select_empty_returns_none(cfg):
    assert select_paper([], cfg, recent_genres=[]) is None
