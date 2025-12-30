# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Callable, TypeVar, Union
from typing_extensions import ParamSpec

from florapi import flatten

CREATE_TABLE_SQL_TEMPLATE = """
CREATE TABLE "{name}" (
{contents}
){extra};
""".strip()

P = ParamSpec("P")
T = TypeVar("T")


def register_adaptors() -> None:
    sqlite3.register_adapter(datetime, adapt_datetime_iso)


def adapt_datetime_iso(dt: datetime) -> str:
    """Adapt datetime.datetime to a ISO 8601 date."""
    return dt.isoformat()


def _require_existing_table(method: Callable[P, T]) -> Callable[P, T]:
    @wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        self, table, *_ = args
        if not self.existing_table(table):
            raise sqlite3.DatabaseError(f"table '{table}' does not exist")
        return method(*args, **kwargs)

    return wrapper


class SQLiteConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._tables = None

    @_require_existing_table
    def insert(
        self,
        table: str,
        columns_or_values: Sequence[str] | Mapping[str, object],
        values: Sequence[object] | None = None,
    ) -> sqlite3.Cursor:
        columns_string = ",".join(columns_or_values)
        if isinstance(columns_or_values, Mapping):
            values = columns_or_values
            values_string = ",".join(f":{col}" for col in columns_or_values)
        else:
            values_string = ",".join("?" * len(columns_or_values))
        self.execute(f"INSERT INTO {table}({columns_string}) VALUES({values_string});", values)

    @_require_existing_table
    def insert_many(
        self, table: str, columns: Sequence[str], values: Sequence[object]
    ) -> sqlite3.Cursor:
        columns_string = ",".join(columns)
        values_string = ",".join("?" * len(columns))
        self.executemany(f"INSERT INTO {table}({columns_string}) VALUES({values_string});", values)

    @_require_existing_table
    def update(
        self,
        table: str,
        values: Mapping[str, object],
        *,
        where: Mapping[str, object],
    ) -> sqlite3.Cursor:
        set_sql = ", ".join(f"{column} = ?" for column in values)
        where_sql = " AND ".join(f"{column} = ?" for column in where)
        args = [*values.values(), *where.values()]
        return self.execute(f"UPDATE {table} SET {set_sql} WHERE {where_sql};", args)

    @_require_existing_table
    def delete(self, table: str, where: Mapping[str, object]) -> sqlite3.Cursor:
        where_sql = " AND ".join(f"{column} = ?" for column in where)
        return self.execute(f"DELETE FROM {table} WHERE {where_sql};", list(where.values()))

    def create_table(
        self,
        name: str,
        columns: Mapping[str, str],
        *,
        constraints: Sequence[str] = (),
        exists_ok: bool = False
    ) -> None:
        lines = [f'    "{col}" {type_}' for col, type_ in columns.items()]
        lines.extend(constraints)
        extra = "IF NOT EXISTS" if exists_ok else ""
        sql = CREATE_TABLE_SQL_TEMPLATE.format(name=name, contents=",\n".join(lines), extra=extra)
        self.execute(sql)
        # Invalidate table cache.
        self._tables = None

    def existing_table(self, table: str) -> bool:
        """Check if a table exists in the database."""
        if self._tables is None:
            self._tables = flatten(self.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        return table in self._tables

    def invalidate_table_cache(self) -> None:
        self._tables = None


def open_sqlite_connection(path: Union[Path, str], factory=SQLiteConnection) -> SQLiteConnection:
    con = sqlite3.connect(path, factory=factory)
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA secure_delete = OFF;")
    con.row_factory = sqlite3.Row
    return con
