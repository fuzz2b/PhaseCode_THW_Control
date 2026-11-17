"""Loads config.json, falling back to defaults for anything missing."""

import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = ROOT_DIR / "config.json"

DEFAULTS = {
    "relay": {"com_port": "auto", "pins_csv": "data/PinsToRelay.csv"},
    "power_supply": {"visa_address": "auto", "serial": None},
    "limits": {
        "max_voltage_V": 12.0,
        "max_current_A": 2.9,
        "protection_voltage_V": 12.0,
        "protection_current_A": 3.0,
    },
    "thw_test": {"voltage_V": 10.0, "inrush_limit_A": 0.01, "max_duration_s": 120},
}


def _merge(base, override):
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load():
    if not CONFIG_PATH.exists():
        return DEFAULTS
    try:
        return _merge(DEFAULTS, json.loads(CONFIG_PATH.read_text()))
    except json.JSONDecodeError as exc:
        # Hardware settings: fail loudly rather than silently using defaults.
        raise RuntimeError(f"config.json is not valid JSON: {exc}") from exc


_CONFIG = _load()


def get(section, key=None):
    """get("limits") -> dict, get("limits", "max_current_A") -> value."""
    section_data = _CONFIG[section]
    return section_data if key is None else section_data[key]


def update(section, key, value):
    """Change one setting and save config.json."""
    _CONFIG[section][key] = value
    CONFIG_PATH.write_text(json.dumps(_CONFIG, indent=2))