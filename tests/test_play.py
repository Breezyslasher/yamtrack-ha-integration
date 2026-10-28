"""Tests for title resolution and provider selection."""

from __future__ import annotations

import json

import pytest

from .conftest import PROVIDERS, provider
from custom_components.yamtrack.play import (
    Candidate,
    normalize_service,
    plex_payload,
    rank_candidates,
    rewrite_link,
    select_offer,
    split_year,
)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Dune 2021", ("Dune", 2021)),
        ("Dune (1984)", ("Dune", 1984)),
        ("Dune", ("Dune", None)),
        ("1917", ("1917", None)),
        ("Blade Runner 2049", ("Blade Runner", 2049)),
    ],
)
def test_split_year(query: str, expected: tuple[str, int | None]) -> None:
    """A trailing year is split from the title."""
    assert split_year(query) == expected


def test_rank_prefers_year_match() -> None:
    """The year in the query beats the search order."""
    candidates = [
        Candidate("438631", "tmdb", "movie", "Dune", year=2021),
        Candidate("841", "tmdb", "movie", "Dune", year=1984),
    ]
    ranked = rank_candidates("Dune", 1984, candidates)
    assert ranked[0].media_id == "841"


def test_rank_keeps_search_order_on_ties() -> None:
    """Without a year the first search result wins a tie."""
    candidates = [
        Candidate("438631", "tmdb", "movie", "Dune"),
        Candidate("841", "tmdb", "movie", "Dune"),
        Candidate("693134", "tmdb", "movie", "Dune: Part Two"),
    ]
    ranked = rank_candidates("dune", None, candidates)
    assert [c.media_id for c in ranked][:2] == ["438631", "841"]


def test_rank_scores_bad_matches_low() -> None:
    """An unrelated title scores below the threshold."""
    ranked = rank_candidates(
        "Dune", None, [Candidate("1", "tmdb", "movie", "The Notebook")]
    )
    assert ranked[0].score < 70


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Disney+", "disneyplus"),
        ("Disney Plus", "disneyplus"),
        ("Netflix Standard with Ads", "netflix"),
        ("Amazon Prime Video with Ads", "amazonprimevideo"),
        ("Apple TV+", "appletvplus"),
    ],
)
def test_normalize_service(name: str, expected: str) -> None:
    """Provider names compare loosely."""
    assert normalize_service(name) == expected


def test_select_offer_uses_priority_and_services() -> None:
    """The highest priority offer on my services wins."""
    offer = select_offer(PROVIDERS, ["Max", "Netflix"], include_free=False)
    assert offer["provider_name"] == "Netflix"
    offer = select_offer(PROVIDERS, ["max"], include_free=False)
    assert offer["provider_name"] == "Max"


def test_select_offer_skips_missing_links_and_paid() -> None:
    """Offers without a link and paid offers are never used."""
    providers = {
        "no_charge": [provider("Netflix", 0, None)],
        "paid": [provider("Apple TV", 1, "https://tv.apple.com/x")],
    }
    assert select_offer(providers, ["Netflix", "Apple TV"], False) is None


def test_select_offer_free_services() -> None:
    """Free services are used only when enabled."""
    providers = {"no_charge": [provider("Tubi TV", 0, "https://tubitv.com/movies/1")]}
    assert select_offer(providers, [], include_free=False) is None
    assert select_offer(providers, [], include_free=True)["provider_name"] == "Tubi TV"


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        (
            "http://www.netflix.com/title/81000001",
            "https://www.netflix.com/watch/81000001",
        ),
        (
            "https://www.netflix.com/gb/title/81000001?x=1",
            "https://www.netflix.com/watch/81000001",
        ),
        (
            "https://www.netflix.com/watch/81000001",
            "https://www.netflix.com/watch/81000001",
        ),
    ],
)
def test_rewrite_netflix(link: str, expected: str) -> None:
    """Netflix title pages become watch links."""
    assert rewrite_link("Netflix", link) == expected


def test_rewrite_leaves_other_providers() -> None:
    """Providers without a rewrite keep their link."""
    assert rewrite_link("Max", "https://play.max.com/x") == "https://play.max.com/x"


def test_plex_payload() -> None:
    """Plex payloads search by exact title in the right library."""
    content_type, content_id = plex_payload("movie", "Movies", "Dune")
    assert content_type == "movie"
    assert json.loads(content_id.removeprefix("plex://")) == {
        "library_name": "Movies",
        "title": "Dune",
        "libtype": "movie",
    }
    content_type, content_id = plex_payload("tv", "TV Shows", "Severance")
    assert json.loads(content_id.removeprefix("plex://"))["libtype"] == "show"
