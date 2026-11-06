"""Adapter: SettingsStore over Postgres (table review_setting_override, migration V7).

Append-only: rows are only ever inserted; the latest per key wins (see settings.base.effective)."""
from psycopg.types.json import Jsonb

from agentic.settings.base import OverrideRow, SettingDef, validate


class PostgresSettingsStore:
    def __init__(self, conn, defs: dict[str, SettingDef]):
        self._conn, self._defs = conn, defs

    def overrides(self) -> list[OverrideRow]:
        """Oldest first (ties by insertion order), so a later row wins a tie in `effective`."""
        rows = self._conn.execute(
            "SELECT key, value, changed_by, changed_at FROM review_setting_override "
            "ORDER BY changed_at, id").fetchall()
        return [OverrideRow(*r) for r in rows]

    def append(self, key: str, value: object, changed_by: str) -> OverrideRow:
        if key not in self._defs:
            raise ValueError(f"unknown setting {key!r}")
        validate(self._defs[key], value)
        r = self._conn.execute(
            "INSERT INTO review_setting_override (key, value, changed_by) VALUES (%s, %s, %s) "
            "RETURNING key, value, changed_by, changed_at", (key, Jsonb(value), changed_by)).fetchone()
        return OverrideRow(*r)

    def close(self) -> None:
        self._conn.close()
