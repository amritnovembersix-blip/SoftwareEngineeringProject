"""Teacher/admin settings: TOML defaults (agentic/config/review-settings.toml), validation, and the
SettingsStore Target that holds overrides (Postgres in production, in memory in the lab)."""
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Protocol

from ..tuning import _read_toml

_FILE = "review-settings.toml"
_TYPES = {"int": int, "float": float, "bool": bool, "str": str}


@dataclass(frozen=True)
class SettingDef:
    key: str
    type: Literal["int", "float", "bool", "str"]
    default: object
    min: int | float | None = None
    max: int | float | None = None
    choices: list | None = None
    description: str = ""
    scope: Literal["system", "classroom"] = "system"
    available: bool = True


@dataclass(frozen=True)
class OverrideRow:
    key: str
    value: object
    changed_by: str
    changed_at: datetime  # timezone-aware


class SettingsStore(Protocol):
    """Target: where overrides live. Rows are append-only; the latest per key wins (see `effective`)."""

    def overrides(self) -> list[OverrideRow]: ...

    def append(self, key: str, value: object, changed_by: str) -> OverrideRow: ...


def _type_ok(type_: str, value: object) -> bool:
    if type_ == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_ == "float":  # an int is fine where a float is wanted
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[type_])


def validate(defn: SettingDef, value: object) -> object:
    k = defn.key
    if not _type_ok(defn.type, value):
        raise ValueError(f"{k}: must be {defn.type}, got {value!r}")
    if defn.type == "float" and not math.isfinite(value):
        raise ValueError(f"{k}: must be a finite number, got {value!r}")
    if defn.min is not None and value < defn.min:
        raise ValueError(f"{k}: must be at least {defn.min}, got {value!r}")
    if defn.max is not None and value > defn.max:
        raise ValueError(f"{k}: must be at most {defn.max}, got {value!r}")
    if defn.choices is not None and value not in defn.choices:
        raise ValueError(f"{k}: must be one of {defn.choices}, got {value!r}")
    if not defn.available and value != defn.default:
        raise ValueError(f"{k}: not available yet (only the default {defn.default!r} is allowed)")
    return value


# Cross-key rules: (hits, window). A count of hits cannot exceed the window it is counted in.
_HITS_WITHIN_WINDOW = (("attention_hits", "attention_window"), ("secure_hits", "secure_window"))


def validate_all(defs: dict[str, SettingDef], effective: dict) -> list[str]:
    """Rules that span keys, checked on a whole effective set. Returns messages naming both keys."""
    errors = []
    for hits, window in _HITS_WITHIN_WINDOW:
        if hits in defs and window in defs and effective[hits] > effective[window]:
            errors.append(f"{hits}: must not be larger than {window} ({effective[hits]!r} > {effective[window]!r})")
    return errors


def load_defs(config_dir: Path) -> dict[str, SettingDef]:
    out: dict[str, SettingDef] = {}
    for key, d in _read_toml(Path(config_dir) / _FILE).items():
        at = f"{_FILE} {key!r}"
        if not isinstance(d, dict):
            raise ValueError(f"{at}: must be a table")
        for req in ("type", "default", "description"):
            if req not in d:
                raise ValueError(f"{at}: missing key {req!r}")
        if d["type"] not in _TYPES:
            raise ValueError(f"{at}: type must be int, float, bool or str, got {d['type']!r}")
        if d.get("scope", "system") not in ("system", "classroom"):
            raise ValueError(f"{at}: scope must be system or classroom, got {d['scope']!r}")
        for bound in ("min", "max"):
            if bound in d and not _type_ok("int" if d["type"] == "int" else "float", d[bound]):
                raise ValueError(f"{at}: {bound} must be {'an int' if d['type'] == 'int' else 'a number'}, got {d[bound]!r}")
        if "choices" in d and not (isinstance(d["choices"], list) and d["choices"]):
            raise ValueError(f"{at}: choices must be a non-empty list")
        if not isinstance(d["description"], str):
            raise ValueError(f"{at}: description must be a string")
        if not isinstance(d.get("available", True), bool):
            raise ValueError(f"{at}: available must be true or false")
        defn = SettingDef(key=key, type=d["type"], default=d["default"], min=d.get("min"), max=d.get("max"),
                          choices=d.get("choices"), description=d["description"],
                          scope=d.get("scope", "system"), available=d.get("available", True))
        try:
            validate(defn, defn.default)
        except ValueError as e:
            raise ValueError(f"{_FILE} default: {e}") from None
        out[key] = defn
    return out


class InMemorySettingsStore:
    """SettingsStore for the lab and tests. `clock` is injectable so "latest wins" is deterministic."""

    def __init__(self, defs: dict[str, SettingDef],
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._defs = defs
        self._clock = clock
        self._rows: list[OverrideRow] = []

    def overrides(self) -> list[OverrideRow]:
        return list(self._rows)

    def append(self, key: str, value: object, changed_by: str) -> OverrideRow:
        if key not in self._defs:
            raise ValueError(f"unknown setting {key!r}")
        row = OverrideRow(key, validate(self._defs[key], value), changed_by, self._clock())
        self._rows.append(row)
        return row


def effective(defs: dict[str, SettingDef], rows: list[OverrideRow]) -> dict:
    """Defaults, then the latest row per key (ties: the later row). Keys not in `defs` are ignored."""
    out = {k: d.default for k, d in defs.items()}
    latest: dict[str, datetime] = {}
    for r in rows:
        if r.key in defs and (r.key not in latest or r.changed_at >= latest[r.key]):
            out[r.key] = r.value
            latest[r.key] = r.changed_at
    return out


def settings_hash(effective: dict) -> str:
    """sha256 of the canonical JSON of the effective settings, to stamp on results."""
    return hashlib.sha256(json.dumps(effective, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
