# tree_walk.py
# Generalized virtualized-list scrolling + expand/collapse walking.
#
# Lifted from the proven walk_tree_and_open_all_files() in
# C:\Projects\developer_tools\base44_export.py, with every Base44-specific
# piece (Monaco content extraction, file saving) stripped out and replaced
# by a generic per-leaf callback — the harness handles "find every leaf
# exactly once", the adapter decides what to do with each one.

import time
from dataclasses import dataclass

from .retry import safe_click, safe_read_text

# Named constants — no bare magic numbers. Values match what the Base44
# reference script proved out in practice.
MAX_TREE_PASSES = 60
STABLE_PASSES_TO_STOP = 3
SCROLL_DELAY_S = 0.5
SCROLL_STEP_PX = 400


@dataclass
class TreeWalkResult:
    """One walk's result: every leaf label visited, how many passes it took, and whether scrolling finished."""

    opened_labels: set[str]
    passes_run: int
    reached_bottom: bool


def walk_virtualized_tree(
    page,
    collapsed_selector: str,
    leaf_selector: str,
    on_leaf,
    scroll_seed_selector: str,
    max_passes: int = MAX_TREE_PASSES,
    stable_passes_to_stop: int = STABLE_PASSES_TO_STOP,
    scroll_delay_s: float = SCROLL_DELAY_S,
) -> TreeWalkResult:
    """
    Repeatedly expand visible collapsed groups, visit every not-yet-seen
    leaf, and scroll to reveal more virtualized rows, until nothing changes
    for `stable_passes_to_stop` passes in a row and scrolling has reached
    the bottom.

    collapsed_selector: matches currently-collapsed expandable groups.
    leaf_selector: matches individual leaf rows (files, list items, etc.).
    on_leaf(page, locator, label): called once per newly-seen leaf — this
        is where adapter-specific work happens (e.g. Base44's Monaco
        extraction + save). The harness itself never inspects leaf content.
    scroll_seed_selector: any selector matching an element inside the
        scrollable container, used to locate that container's nearest
        scrollable ancestor.
    """
    # Not resolved once up front: the scrollable container may not overflow
    # yet on pass 1 (e.g. before any folder is expanded), and only become
    # scrollable once more rows exist — re-check each pass until found, so
    # content that's only revealed after expansion still gets scrolled into
    # view instead of being silently missed.
    scroll_handle = None

    opened_labels = set()
    stable_passes = 0
    reached_bottom = True
    pass_num = 0

    for _pass_num in range(1, max_passes + 1):
        made_progress = False

        collapsed = page.locator(collapsed_selector)
        for i in range(collapsed.count()):
            if safe_click(collapsed.nth(i)):
                made_progress = True
                time.sleep(scroll_delay_s)

        leaves = page.locator(leaf_selector)
        for i in range(leaves.count()):
            leaf = leaves.nth(i)
            label = safe_read_text(leaf)
            if not label or label in opened_labels:
                continue
            if safe_click(leaf):
                made_progress = True
                time.sleep(scroll_delay_s)
                on_leaf(page, leaf, label)
            opened_labels.add(label)

        if scroll_handle is None:
            scroll_handle = _get_scrollable_ancestor(page, scroll_seed_selector)

        reached_bottom = True
        if scroll_handle is not None:
            reached_bottom = _is_scrolled_to_bottom(page, scroll_handle)
            if not reached_bottom:
                _scroll_by(page, scroll_handle, SCROLL_STEP_PX)
                made_progress = True
                time.sleep(scroll_delay_s)

        stable_passes = 0 if made_progress else stable_passes + 1
        if stable_passes >= stable_passes_to_stop and reached_bottom:
            break

    if scroll_handle is None:
        print("  [note] No scrollable container detected in any pass - assuming everything fit without scrolling.")

    return TreeWalkResult(opened_labels=opened_labels, passes_run=pass_num, reached_bottom=reached_bottom)


def _get_scrollable_ancestor(page, seed_selector: str):
    """Find the nearest scrollable ancestor of the first element matching seed_selector."""
    handle = page.evaluate_handle(
        """
        (seedSelector) => {
            const items = document.querySelectorAll(seedSelector);
            if (!items.length) return null;
            let el = items[0];
            while (el && el !== document.documentElement) {
                if (el.scrollHeight > el.clientHeight + 20) return el;
                el = el.parentElement;
            }
            return null;
        }
        """,
        seed_selector,
    )
    return handle if handle.as_element() is not None else None


def _is_scrolled_to_bottom(page, handle) -> bool:
    return page.evaluate("(el) => el.scrollTop + el.clientHeight >= el.scrollHeight - 5", handle)


def _scroll_by(page, handle, amount_px: int) -> None:
    handle.evaluate("(el, amt) => { el.scrollTop += amt; }", amount_px)
