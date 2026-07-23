# selector_discovery.py
# Generic discover-mode runner — probe a set of candidate selectors against
# a live page and report match counts. Discover mode must be run and its
# output eyeballed before every real adapter run, not just the first time:
# sites change their DOM without notice, and a stale selector should produce
# a loud warning, not a silent bad run. See the Build Spec, Section 2.4.
#
# Known M1 limitation: only zero-match selectors are flagged. The spec also
# calls for flagging "suspiciously-high" match counts, but there's no real
# adapter run yet to establish what "high" means for any given site — that
# needs historical browser_tasks counts (M2+), not an invented threshold now.

from dataclasses import dataclass
from datetime import datetime


@dataclass
class DiscoveryReport:
    """One discover-mode run's result: match counts per selector, and which ones hit zero."""

    counts: dict[str, int]
    zero_match: list[str]
    checked_at: str


def probe_selectors(page, selectors: dict[str, str]) -> DiscoveryReport:
    """
    Count how many elements each candidate selector matches on the current
    page. `selectors` maps a human-readable label to a CSS/testid selector
    string. Prints every count, with a loud warning for any that hit zero.
    """
    counts = {}
    zero_match = []

    for label, selector in selectors.items():
        count = page.locator(selector).count()
        counts[label] = count
        if count == 0:
            zero_match.append(label)
            print(f"  [WARNING] '{label}' ({selector!r}) matched 0 elements - selector may be stale.")
        else:
            print(f"  '{label}' ({selector!r}): {count} match(es)")

    return DiscoveryReport(
        counts=counts, zero_match=zero_match, checked_at=datetime.now().isoformat(timespec="seconds")
    )  # noqa: E501
