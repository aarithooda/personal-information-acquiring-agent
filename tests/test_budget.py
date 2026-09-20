from datetime import timedelta

import pytest
from fakes import T0

from pia.briefing.budget import also_budget, days_since, headline_budget, shortlist_size


@pytest.mark.parametrize(
    ("days", "expected"),
    [(0.1, 1), (1, 1), (2, 3), (3, 4), (5, 7), (10, 15), (14, 21)],
)
def test_headline_budget_scales_with_time_away(days, expected):
    assert headline_budget(days) == expected


def test_one_day_away_gives_one_development_and_ten_days_gives_fifteen():
    """The example from the project brief."""
    assert headline_budget(1) == 1
    assert headline_budget(10) == 15


def test_days_since_uses_the_checkpoint():
    assert days_since(T0 - timedelta(days=10), T0) == pytest.approx(10)
    assert days_since(T0 - timedelta(hours=12), T0) == pytest.approx(0.5)


def test_first_run_is_treated_as_the_default_lookback():
    assert days_since(None, T0) == pytest.approx(3)


def test_absences_longer_than_the_fetch_lookback_are_capped():
    # We never fetch further back than 14 days, so we never promise more than 14 days of news.
    assert days_since(T0 - timedelta(days=60), T0) == pytest.approx(14)


def test_a_checkpoint_in_the_future_does_not_go_negative():
    assert days_since(T0 + timedelta(days=1), T0) == 0


def test_secondary_and_shortlist_budgets_follow_the_headline_budget():
    assert also_budget(1) >= 2
    assert also_budget(15) == 2 * 15
    assert shortlist_size(1) > 1  # the editor needs alternatives to choose from
    assert shortlist_size(15) > 15
