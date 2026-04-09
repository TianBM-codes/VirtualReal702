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
        try:
            with self._get_conn() as conn:
                return conn.execute(
                    "SELECT * FROM instances WHERE instance_name=?", (instance_name,)
                ).fetchone()
        except Exception:
            return None

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
            
    def has_nodal_block(self, step: str, field: str, instance: str) -> bool:
        """Return True if result_blocks has a NODAL entry for (step, field, instance)."""
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM result_blocks
                WHERE step_name=? AND field_name=? AND instance_name=? AND position='NODAL'
                LIMIT 1
                """,
                (step, field, instance),
            ).fetchone()
        return row is not None

    def get_step_info(self, step_name: str):
        """Return the steps row for step_name, or None."""
        try:
            with self._get_conn() as conn:
                return conn.execute(
                    "SELECT * FROM steps WHERE step_name=?", (step_name,)
                ).fetchone()
        except Exception:
            return None

    def get_fields_by_instance(self, step: str, instance: str):
        """
        Return fields available for a specific (step, instance) combination.
        Joins result_blocks (instance-level positions) with result_files (components).
        Returns rows with columns: field_name, positions (comma-separated), components (JSON).
        """
        with self._get_conn() as conn:
            return conn.execute(
                """
                SELECT rb.field_name,
                       GROUP_CONCAT(DISTINCT rb.position) AS positions,
                       rf.components
                FROM result_blocks rb
                JOIN result_files rf
                  ON rb.step_name = rf.step_name AND rb.field_name = rf.field_name
                WHERE rb.step_name = ? AND rb.instance_name = ?
                GROUP BY rb.field_name
                ORDER BY rb.field_name
                """,
                (step, instance),
            ).fetchall()

    def get_result_file(self, step: str, field: str):
        """Return the result_files row for (step, field), or None."""
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM result_files WHERE step_name=? AND field_name=?",
                (step, field),
            ).fetchone()

    def get_overview(self):
        """
        Returns instances, steps, and available fields for meta/overview.
        Gracefully returns empty lists if the manifest schema is not yet
        initialised (e.g. workspace exists but L1 packing never completed).
        """
        def _safe_query(conn, sql):
            try:
                return [dict(r) for r in conn.execute(sql).fetchall()]
            except Exception:
                return []

        with self._get_conn() as conn:
            instances = _safe_query(conn, "SELECT * FROM instances")
            steps     = _safe_query(conn, "SELECT * FROM steps")
            fields    = _safe_query(
                conn,
                "SELECT DISTINCT field_name, components, positions FROM result_files",
            )
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

    # ── user_fields (scalar value per element set) ─────────────────────────────

    def _ensure_user_fields_table(self, conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_fields (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                instance_name TEXT NOT NULL,
                value REAL NOT NULL,
                element_labels BLOB NOT NULL,
                created_at TEXT,
                UNIQUE(name, instance_name)
            )
        """)

    def save_user_field(
        self,
        name: str,
        instance_name: str,
        value: float,
        element_labels: "np.ndarray",  # int32 1D
    ) -> int:
        """
        Insert or replace a named user field.
        element_labels: all elements in the set (get value `value`).
        Returns the row id.
        """
        blob = zlib.compress(np.asarray(element_labels, dtype=np.int32).tobytes())
        conn = self._get_conn()
        try:
            self._ensure_user_fields_table(conn)
            conn.execute(
                """
                INSERT INTO user_fields (name, instance_name, value, element_labels, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(name, instance_name) DO UPDATE SET
                    value = excluded.value,
                    element_labels = excluded.element_labels,
                    created_at = excluded.created_at
                """,
                (name, instance_name, float(value), blob),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM user_fields WHERE name=? AND instance_name=?",
                (name, instance_name),
            ).fetchone()
            return row["id"]
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_user_field(self, name: str, instance_name: str):
        """
        Returns dict {id, name, instance_name, value, element_labels (int32 ndarray)}
        or None if not found.
        """
        try:
            with self._get_conn() as conn:
                self._ensure_user_fields_table(conn)
                row = conn.execute(
                    "SELECT * FROM user_fields WHERE name=? AND instance_name=?",
                    (name, instance_name),
                ).fetchone()
        except Exception:
            return None
        if row is None:
            return None
        labels = np.frombuffer(
            zlib.decompress(row["element_labels"]), dtype=np.int32
        ).copy()
        return {
            "id":            row["id"],
            "name":          row["name"],
            "instance_name": row["instance_name"],
            "value":         row["value"],
            "element_labels": labels,
        }

    def list_user_fields(self, instance_name: str = None):
        """
        Returns list of dicts {id, name, instance_name, value, created_at}.
        Optionally filtered by instance_name.
        """
        try:
            with self._get_conn() as conn:
                self._ensure_user_fields_table(conn)
                if instance_name:
                    rows = conn.execute(
                        "SELECT id, name, instance_name, value, created_at "
                        "FROM user_fields WHERE instance_name=? ORDER BY name",
                        (instance_name,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, name, instance_name, value, created_at "
                        "FROM user_fields ORDER BY name",
                    ).fetchall()
        except Exception:
            return []
        return [dict(r) for r in rows]

    def delete_user_field(self, name: str, instance_name: str) -> bool:
        """Delete a named user field. Returns True if it existed."""
        conn = self._get_conn()
        try:
            self._ensure_user_fields_table(conn)
            cur = conn.execute(
                "DELETE FROM user_fields WHERE name=? AND instance_name=?",
                (name, instance_name),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
