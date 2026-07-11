# cdp_connect.py
# Attach to an already-running, Marvin-controlled Chrome session via CDP.
#
# This never launches its own browser and never logs in on Marvin's behalf —
# Marvin starts Chrome by hand with --remote-debugging-port and a persistent
# --user-data-dir, logs into whatever site he's working with, and this module
# just attaches to that existing tab. Credential custody stays entirely with
# Marvin, same boundary as a password manager. See the Build Spec, Section 2.3.
#
# Generalized from the proven pattern in C:\Projects\developer_tools\
# base44_export.py's connect_to_existing_chrome().

from contextlib import contextmanager

from playwright.sync_api import sync_playwright


@contextmanager
def connect_to_chrome(cdp_url: str, url_hint: str | None = None):
    """
    Attach to an already-running Chrome instance over CDP and yield its page.

    cdp_url: e.g. "http://localhost:9222" — the --remote-debugging-port URL.
    url_hint: a substring to find an already-open tab (e.g. "base44.com");
        if no matching tab is found, or url_hint is None, a new tab is
        opened in the existing browser context instead.

    On exit, only detaches from the CDP session — Marvin's Chrome window
    stays open exactly as it was, nothing is closed on his behalf.
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = _find_or_create_page(context, url_hint)
        yield page
        browser.close()


def _find_or_create_page(context, url_hint: str | None):
    """Return the first page whose URL contains url_hint, or a new page if none match."""
    if url_hint is not None:
        for page in context.pages:
            if url_hint in page.url:
                return page
    return context.new_page()
