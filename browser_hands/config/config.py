# config.py
# Loads per-adapter site configuration from sites.yaml — target URL(s), CDP
# port, output location. Kept separate from harness/cdp_connect.py: config
# loading is a different concern from managing the CDP connection itself.

from pathlib import Path

import yaml

SITES_CONFIG_PATH = Path(__file__).parent / "sites.yaml"


def load_sites_config(path: Path = SITES_CONFIG_PATH) -> dict:
    """
    Read sites.yaml and return its parsed contents — one top-level key per
    adapter. Returns an empty dict if the file is missing or has no real
    entries yet (M1 ships only a commented-out template; M2+ adapters add
    real entries as they're built).
    """
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
