"""How much to show, as a function of how long the user has been away.

These are scale factors, not per-item cutoffs. Nothing here decides whether a specific
item is good enough; it only sets how many slots a gap of N days deserves. The editor
step can fill fewer slots than offered when little of substance happened.
"""

from datetime import datetime, timedelta

from pia.collect import FIRST_RUN_LOOKBACK, MAX_LOOKBACK

HEADLINES_PER_DAY = 1.5  # "worth knowing" slots per day away: 1 day -> 1, 10 days -> 15
ALSO_PER_HEADLINE = 2  # one-line bullets offered per headline slot
_DAY = timedelta(days=1)


def days_since(checkpoint: datetime | None, now: datetime) -> float:
    """Days the briefing covers. Capped like fetching is: we never look back further than
    MAX_LOOKBACK, so we never promise more than that. A first run counts as the default lookback."""
    span = FIRST_RUN_LOOKBACK if checkpoint is None else min(now - checkpoint, MAX_LOOKBACK)
    return max(span, timedelta(0)) / _DAY


def headline_budget(days: float) -> int:
    return max(1, int(HEADLINES_PER_DAY * days + 1e-9))


def also_budget(headlines: int) -> int:
    return ALSO_PER_HEADLINE * headlines


def shortlist_size(headlines: int) -> int:
    """How many top-ranked candidates the editor sees: enough alternatives to choose between."""
    return 2 * headlines + 2
