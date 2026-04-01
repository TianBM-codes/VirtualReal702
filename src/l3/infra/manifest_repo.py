import sqlite3
import os
import zlib
import numpy as np

class ManifestRepo:
    """
    Repository for interacting with the SQLite manifest.db of a specific ODB.
    """
    def __init__(self, workspace: str):
        self.db_path = os.path.join(workspace, "manifest.db")
    
    def _get_conn(self):
        # timeout=5.0 is essential for SQLite concurrency under load.
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        
        # Crucial for concurrent Gunicorn workers writing to SQLite
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def get_instance_info(self, instance_name: str):
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM instances WHERE instance_name=?", (instance_name,)
            ).fetchone()

    def get_result_block(self, step: str, field: str, instance: str, position: str, elem_type: str = None):
        query = """
            SELECT * FROM result_blocks 
            WHERE step_name=? AND field_name=? AND instance_name=? AND position=?
        """
        params = [step, field, instance, position]
        if elem_type:
            query += " AND elem_type=?"
            params.append(elem_type)
        else:
            query += " AND elem_type IS NULL"
            
        with self._get_conn() as conn:
            return conn.execute(query, params).fetchone()
            
    def get_overview(self):
        """Returns instances, steps, and available fields for meta/overview."""
        with self._get_conn() as conn:
            instances = [dict(r) for r in conn.execute("SELECT * FROM instances").fetchall()]
            steps = [dict(r) for r in conn.execute("SELECT * FROM steps").fetchall()]
            fields = [
                dict(r) for r in conn.execute(
                    "SELECT DISTINCT field_name, components, positions FROM result_files"
                ).fetchall()
            ]
        return {"instances": instances, "steps": steps, "fields": fields}

    def _ensure_user_tables(self, conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT,
                total_elem_count INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_set_instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                us_id INTEGER NOT NULL REFERENCES user_sets(id),
                instance_name TEXT NOT NULL,
                elem_count INTEGER,
                render_rows BLOB,
                elem_rows  BLOB,
                etype_rows BLOB
            )
        """)
        # Migration: add etype_rows if table pre-existed without it
        try:
            conn.execute("ALTER TABLE user_set_instances ADD COLUMN etype_rows BLOB")
        except Exception:
            pass  # column already exists

    def save_user_set(self, set_name: str, total_elem_count: int, instance_data: list):
        """
        Saves a BBox selection as a user set.

        instance_data: list of dicts:
        {
            "instance_name": str,
            "elem_count": int,
            "render_rows": np.ndarray (int32) — all matched render face indices, len = render_face_count
            "elem_rows":   np.ndarray (int32) — unique element row indices, len = elem_count
            "etype_rows":  np.ndarray (S8, optional) — etype per unique element, same len as elem_rows
        }
        elem_rows and etype_rows are 1-to-1: (etype_rows[i], elem_rows[i]) uniquely identifies element i.
        """
        conn = self._get_conn()
        try:
            self._ensure_user_tables(conn)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO user_sets (name, created_at, total_elem_count) VALUES (?, datetime('now'), ?)",
                (set_name, total_elem_count),
            )
            us_id = cursor.lastrowid

            for idata in instance_data:
                render_blob = zlib.compress(idata["render_rows"].astype(np.int32).tobytes())
                elem_blob   = zlib.compress(idata["elem_rows"].astype(np.int32).tobytes())
                etype_raw   = idata.get("etype_rows")
                etype_blob  = zlib.compress(np.asarray(etype_raw, dtype="S8").tobytes()) \
                              if etype_raw is not None else None
                cursor.execute(
                    """
                    INSERT INTO user_set_instances
                    (us_id, instance_name, elem_count, render_rows, elem_rows, etype_rows)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (us_id, idata["instance_name"], idata["elem_count"],
                     render_blob, elem_blob, etype_blob),
                )

            conn.commit()
            return us_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
