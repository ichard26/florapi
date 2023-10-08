# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final, Optional

from pydantic import BaseModel

from florapi import utc_now
from florapi.sqlite import SQLiteConnection

RateLimits = dict[int, int]


class RateLimitWindow(BaseModel):
    duration: int
    limit: int
    value: int
    expiry: datetime


class RateLimiter:
    MINUTE: Final = 1
    HOUR: Final = 60
    DAY: Final = 1440

    class SQLiteBackend:
        def __init__(self, db: SQLiteConnection, table: str = "_ratelimits", max_windows: int = 5000) -> None:
            self.db = db
            self.table = table
            self.max_windows = max_windows
            self.commit = db.commit
            if not db.existing_table(table):
                db.create_table(
                    table,
                    columns={
                        "key": "TEXT NOT NULL",
                        "duration": "INTEGER NOT NULL",
                        "value": "TEXT NOT NULL",
                        "expiry": "TEXT NOT NULL",
                    },
                    constraints=['PRIMARY KEY("key", "expiry")']
                )

        def get_window(self, key: str, duration: int, limit: int) -> Optional[RateLimitWindow]:
            cur = self.db.execute(
                f"SELECT rowid, value, expiry FROM {self.table} WHERE key = ? AND duration = ?;",
                [key, duration]
            )
            if row := cur.fetchone():
                return RateLimitWindow(duration=duration, limit=limit, value=row[1], expiry=row[2])
            return None

        def create_window(self, key: str, duration: int, limit: int, expiry: datetime) -> RateLimitWindow:
            db_entry = {"key": key, "duration": duration, "value": 0, "expiry": expiry}
            self.db.insert(self.table, db_entry)
            return RateLimitWindow(duration=duration, limit=limit, value=0, expiry=expiry)

        def update_window_key(self, key: str, by: int) -> None:
            self.db.execute(
                f"UPDATE {self.table} SET value = value + ? WHERE key = ?;", [by, key]
            )

        def delete_window(self, key: str, duration: int) -> None:
            self.db.execute(
                f"DELETE FROM {self.table} WHERE key = ? AND duration = ?;", [key, duration]
            )

        def prune(self) -> int:
            count = self.db.execute(f"SELECT COUNT(*) FROM {self.table};").fetchone()[0]
            if (to_prune := count - self.max_windows) > 0:
                self.db.execute(f"DELETE FROM {self.table} ORDER BY expiry LIMIT ?;", [to_prune])

    def __init__(self, key_prefix: str, limits: RateLimits, db: sqlite3.Connection) -> None:
        self.key_prefix = key_prefix + ":"
        self.limits = limits
        self.backend = RateLimiter.SQLiteBackend(db)

    def update(self, key: str, by: int = 1) -> None:
        """Increment all windows.

        New windows are created as needed (none existed or old one was expired).
        """
        for duration in self.limits:
            self._get_or_create_window(self.key_prefix + key, duration)
        self.backend.update_window_key(self.key_prefix + key, by)
        self.backend.prune()
        self.backend.commit()

    def should_block(self, key: str) -> bool:
        """Return whether at least one limit has been reached."""
        return len(self.reached_limits(key)) > 0

    def update_and_check(self, key: str, by: int = 1) -> bool:
        """update() and should_block() combined."""
        block = self.should_block(key)
        self.update(key, by)
        return block

    def windows(self, key: str) -> Sequence[RateLimitWindow]:
        """Return rate limit windows."""
        wins = [self._get_or_create_window(self.key_prefix + key, duration) for duration in self.limits]
        self.backend.commit()
        return wins

    def reached_limits(self, key: str) -> Sequence[RateLimitWindow]:
        """Like windows() but returns only windows whose limit has been reached."""
        return [w for w in self.windows(key) if w.value >= self.limits[w.duration]]

    def _get_or_create_window(self, key: str, duration: int) -> RateLimitWindow:
        limit = self.limits[duration]
        if stored := self.backend.get_window(key, duration, limit):
            if stored.expiry > utc_now():
                return stored

            self.backend.delete_window(key, duration)
        return self.backend.create_window(
            key, duration, limit, expiry=utc_now() + timedelta(minutes=duration)
        )
