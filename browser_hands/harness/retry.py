# retry.py
# Bounded-timeout click/read wrappers — no bare Playwright calls with
# unbounded waits, ever. This was the direct cause of a real hang during the
# Base44 export build; every adapter must use these instead of calling
# locator.click()/inner_text() directly. See the Build Spec, Section 2.2.

# Named so a timeout value is never a bare magic number at the call site.
# Match the values already proven out in the Base44 reference script.
DEFAULT_CLICK_TIMEOUT_MS = 2000
DEFAULT_READ_TIMEOUT_MS = 1000


def safe_click(locator, timeout_ms: int = DEFAULT_CLICK_TIMEOUT_MS) -> bool:
    """
    Click a locator with a bounded timeout. Returns True on success, False on
    a timeout or any other Playwright error — never raises, so a walk over
    many rows can skip a stuck one instead of hanging or crashing entirely.
    """
    try:
        locator.click(timeout=timeout_ms)
        return True
    except Exception:
        return False


def safe_read_text(locator, timeout_ms: int = DEFAULT_READ_TIMEOUT_MS) -> str | None:
    """
    Read a locator's inner_text with a bounded timeout. Returns None on a
    timeout or any other Playwright error, instead of raising.
    """
    try:
        return locator.inner_text(timeout=timeout_ms)
    except Exception:
        return None
