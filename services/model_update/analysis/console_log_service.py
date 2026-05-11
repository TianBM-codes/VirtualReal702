from __future__ import annotations

import html
import time
from typing import Iterable, Optional

from db import ensure_tables_exist, get_connection

_MAX_LOG_TEXT_LENGTH = 2048


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_plain_lines(lines: Optional[Iterable[object]]) -> list[str]:
    result: list[str] = []
    for item in list(lines or []):
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def build_console_log_html(title: object, lines: Optional[Iterable[object]] = None) -> str:
    title_text = str(title or "").strip() or "Console"
    detail_lines = _normalize_plain_lines(lines)

    plain_parts = [title_text]
    plain_parts.extend(detail_lines)
    plain_text = "\n".join(plain_parts)
    if len(plain_text) > 1500:
        plain_text = plain_text[:1497].rstrip() + "..."

    escaped_title = html.escape(title_text, quote=True)
    escaped_body = html.escape(plain_text, quote=True).replace("\n", "<br/>")
    escaped_body = escaped_body.removeprefix(f"{escaped_title}<br/>")
    content = f"<strong>{escaped_title}</strong>"
    if escaped_body and escaped_body != escaped_title:
        content += f"<br/>{escaped_body}"
    html_text = f"<div>{content}</div>"
    if len(html_text) > _MAX_LOG_TEXT_LENGTH:
        html_text = html_text[: _MAX_LOG_TEXT_LENGTH - 3] + "..."
    return html_text


def append_console_log(project_id: int, html_text: str, *, event_time_ms: Optional[int] = None) -> dict:
    ensure_tables_exist()
    resolved_time = int(event_time_ms if event_time_ms is not None else _now_ms())
    resolved_html = str(html_text or "").strip() or "<div></div>"
    if len(resolved_html) > _MAX_LOG_TEXT_LENGTH:
        resolved_html = resolved_html[: _MAX_LOG_TEXT_LENGTH - 3] + "..."

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO t_mt_py_console_log (pid, `time`, log_text)
            VALUES (%s, %s, %s)
            """,
            (int(project_id), resolved_time, resolved_html),
        )
        conn.commit()
        return {
            "project_id": int(project_id),
            "time": resolved_time,
            "log_text": resolved_html,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def write_console_event(project_id: int, title: object, lines: Optional[Iterable[object]] = None) -> dict:
    return append_console_log(
        project_id=project_id,
        html_text=build_console_log_html(title, lines),
    )


def safe_write_console_event(project_id: int, title: object, lines: Optional[Iterable[object]] = None) -> None:
    try:
        write_console_event(project_id, title, lines)
    except Exception:
        return
