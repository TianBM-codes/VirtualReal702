from __future__ import annotations

from functools import lru_cache
from typing import Any

from db import get_connection


@lru_cache(maxsize=1)
def _work_condition_project_columns() -> frozenset[str]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SHOW COLUMNS FROM t_mt_work_condition_project")
        return frozenset(str(row[0]) for row in cursor.fetchall() if row and row[0])
    finally:
        cursor.close()
        conn.close()


def update_work_condition_project_status(
    project_id: int,
    *,
    cursor=None,
    **fields: Any,
) -> None:
    available_columns = _work_condition_project_columns()
    updates = {
        str(key): value
        for key, value in (fields or {}).items()
        if value is not None and str(key) in available_columns
    }
    if not updates:
        return

    owns_connection = cursor is None
    conn = None
    local_cursor = cursor
    if owns_connection:
        conn = get_connection()
        local_cursor = conn.cursor()

    try:
        assignments = ", ".join(f"{column} = %s" for column in updates.keys())
        params = list(updates.values())
        params.append(int(project_id))
        local_cursor.execute(
            f"""
            UPDATE t_mt_work_condition_project
            SET {assignments}
            WHERE project_id = %s
            """,
            tuple(params),
        )
        if owns_connection and conn is not None:
            conn.commit()
    except Exception:
        if owns_connection and conn is not None:
            conn.rollback()
        raise
    finally:
        if owns_connection and local_cursor is not None:
            local_cursor.close()
        if owns_connection and conn is not None:
            conn.close()
