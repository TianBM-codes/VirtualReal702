import json
import sqlite3
import os
import zlib
import numpy as np

class ManifestRepo:
    """
    Repository for interacting with the SQLite manifest.db of a specific ODB.
    """
    def __init__(self, workspace: str):
        self.workspace = workspace
        self.db_path = os.path.join(workspace, "manifest.db")
    
    def _get_conn(self):
        # timeout=5.0 is essential for SQLite concurrency under load.
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        # Migration: add source column if this db was created before the feature
        try:
            conn.execute(
                "ALTER TABLE result_files ADD COLUMN source TEXT NOT NULL DEFAULT 'odb'"
            )
            conn.commit()
        except Exception:
            pass
        # Migration: create display_names table if absent
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS display_names (
                    instance     TEXT NOT NULL,
                    scheme       TEXT NOT NULL,
                    legend_key   TEXT NOT NULL,
                    display_name TEXT,
                    color_r      REAL,
                    color_g      REAL,
                    color_b      REAL,
                    PRIMARY KEY (instance, scheme, legend_key)
                )
            """)
            conn.commit()
        except Exception:
            pass
        # Migration: add color columns to existing display_names table
        for col in ("color_r REAL", "color_g REAL", "color_b REAL"):
            try:
                conn.execute(f"ALTER TABLE display_names ADD COLUMN {col}")
                conn.commit()
            except Exception:
                pass
        return conn

    def get_instance_info(self, instance_name: str):
        try:
            with self._get_conn() as conn:
                return conn.execute(
                    "SELECT * FROM instances WHERE instance_name=?", (instance_name,)
                ).fetchone()
        except Exception:
            return None

    def get_geom_path(self, instance_name: str):
        """Return absolute L1 geometry HDF5 path from manifest, or None if not found."""
        row = self.get_instance_info(instance_name)
        if row and row["geom_path"]:
            return os.path.join(self.workspace, row["geom_path"])
        return None

    @staticmethod
    def _rg_clause(result_group, prefix=""):
        """生成 result_group 过滤子句和参数。
        result_group=None → IS NULL（旧数据兼容）；否则 =?。
        prefix: 表别名前缀，如 'rb.'。
        """
        col = "{}result_group".format(prefix)
        if result_group is None:
            return "{} IS NULL".format(col), []
        return "{} = ?".format(col), [result_group]

    def get_result_block(self, step: str, field: str, instance: str,
                         position: str, elem_type: str = None,
                         result_group: str = None):
        rg_clause, rg_params = self._rg_clause(result_group)
        query = (
            "SELECT * FROM result_blocks"
            " WHERE step_name=? AND field_name=? AND instance_name=?"
            " AND position=? AND {}".format(rg_clause)
        )
        params = [step, field, instance, position] + rg_params
        if elem_type:
            query += " AND elem_type=?"
            params.append(elem_type)
        else:
            query += " AND elem_type IS NULL"
        with self._get_conn() as conn:
            return conn.execute(query, params).fetchone()

    def has_nodal_block(self, step: str, field: str, instance: str,
                        result_group: str = None) -> bool:
        """Return True if result_blocks has a NODAL entry for (step, field, instance)."""
        rg_clause, rg_params = self._rg_clause(result_group)
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM result_blocks"
                " WHERE step_name=? AND field_name=? AND instance_name=?"
                " AND position='NODAL' AND {} LIMIT 1".format(rg_clause),
                [step, field, instance] + rg_params,
            ).fetchone()
        return row is not None

    def get_step_info(self, step_name: str, result_group: str = None):
        """Return the steps row for (result_group, step_name), or None."""
        rg_clause, rg_params = self._rg_clause(result_group)
        try:
            with self._get_conn() as conn:
                return conn.execute(
                    "SELECT * FROM steps WHERE step_name=? AND {}".format(rg_clause),
                    [step_name] + rg_params,
                ).fetchone()
        except Exception:
            return None

    def get_frames(self, step_name: str, result_group: str = None) -> list:
        """Return all frame rows for (result_group, step_name), ordered by frame_idx."""
        rg_clause, rg_params = self._rg_clause(result_group)
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM frames WHERE step_name=? AND {}"
                " ORDER BY frame_idx".format(rg_clause),
                [step_name] + rg_params,
            ).fetchall()

    def get_fields_by_instance(self, step: str, instance: str,
                               result_group: str = None):
        """
        Return fields available for a specific (result_group, step, instance).
        Joins result_blocks with result_files (both scoped by result_group).
        """
        rg_rb, rg_rb_p = self._rg_clause(result_group, prefix="rb.")
        rg_rf, rg_rf_p = self._rg_clause(result_group, prefix="rf.")
        with self._get_conn() as conn:
            return conn.execute(
                """
                SELECT rb.field_name,
                       GROUP_CONCAT(DISTINCT rb.position) AS positions,
                       rf.components
                FROM result_blocks rb
                JOIN result_files rf
                  ON rb.result_group IS rf.result_group
                  AND rb.step_name = rf.step_name
                  AND rb.field_name = rf.field_name
                WHERE rb.step_name = ? AND rb.instance_name = ?
                  AND {} AND {}
                GROUP BY rb.field_name
                ORDER BY rb.field_name
                """.format(rg_rb, rg_rf),
                [step, instance] + rg_rb_p + rg_rf_p,
            ).fetchall()

    def get_result_file(self, step: str, field: str, result_group: str = None):
        """Return the result_files row for (result_group, step, field), or None."""
        rg_clause, rg_params = self._rg_clause(result_group)
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM result_files"
                " WHERE step_name=? AND field_name=? AND {}".format(rg_clause),
                [step, field] + rg_params,
            ).fetchone()

    def list_result_groups(self) -> list:
        """Return all result_group names present in result_group_meta."""
        try:
            with self._get_conn() as conn:
                return [r["result_group"] for r in conn.execute(
                    "SELECT result_group FROM result_group_meta ORDER BY created_at"
                ).fetchall()]
        except Exception:
            return []

    def get_result_group_meta(self, result_group: str):
        """Return result_group_meta row, or None."""
        try:
            with self._get_conn() as conn:
                return conn.execute(
                    "SELECT * FROM result_group_meta WHERE result_group=?",
                    (result_group,),
                ).fetchone()
        except Exception:
            return None

    def update_result_group_meta_display_name(self, result_group: str,
                                               display_name: str) -> None:
        """Update display_name in result_group_meta. No-op if row doesn't exist."""
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE result_group_meta SET display_name=? WHERE result_group=?",
                    (display_name, result_group),
                )
        except Exception:
            pass

    def get_overview(self, result_group: str = None):
        """
        Returns instances, steps (scoped by result_group), and fields.
        result_group=None → returns geometry info + legacy flat results.
        Pass a result_group to get steps/fields scoped to that group.
        """
        def _safe(conn, sql, params=()):
            try:
                return [dict(r) for r in conn.execute(sql, params).fetchall()]
            except Exception:
                return []

        rg_clause, rg_params = self._rg_clause(result_group)

        with self._get_conn() as conn:
            instances = _safe(conn, "SELECT * FROM instances")
            steps = _safe(
                conn,
                "SELECT * FROM steps WHERE {}".format(rg_clause),
                rg_params,
            )
            fields = _safe(
                conn,
                "SELECT DISTINCT field_name, components, invariants, positions, source"
                " FROM result_files WHERE {}".format(rg_clause),
                rg_params,
            )
        # Deserialize JSON-string columns in instances
        for inst in instances:
            for key in ("bbox_min", "bbox_max"):
                v = inst.get(key)
                if isinstance(v, str):
                    try:
                        inst[key] = json.loads(v)
                    except Exception:
                        pass
        return {"instances": instances, "steps": steps, "fields": self._group_invariant_fields(fields)}

    @staticmethod
    def _group_invariant_fields(fields: list) -> list:
        """
        Separate scalar-invariant fields (e.g. S_MISES) from their parent (e.g. S).
        Invariants go into a dedicated 'invariants' list on the parent, NOT into
        'components'. This lets the frontend route correctly:
          - components → field=S & component_idx=N  (column in S.h5)
          - invariants → field=S_MISES              (separate file, no component_idx)

        A field is an invariant of parent P when its components list is empty,
        its name starts with P + "_", and P exists in the same list.
        """
        # MAGNITUDE is the only invariant computable on the fly at L3 query time
        # (L2 norm of any NODAL vector field — e.g. U, RF, V).
        # Other invariants (MISES, principal, …) need --invariants full pre-extraction.
        _ONTHEFLY_INVARIANTS = {"MAGNITUDE"}

        parsed = []
        for f in fields:
            raw = f.get("components", "[]") or "[]"
            try:
                comps = json.loads(raw) if isinstance(raw, str) else (raw or [])
            except Exception:
                comps = []
            raw_db_inv = f.get("invariants", "[]") or "[]"
            try:
                db_invs = json.loads(raw_db_inv) if isinstance(raw_db_inv, str) else (raw_db_inv or [])
            except Exception:
                db_invs = []
            parsed.append({**f, "_comps": comps, "_db_invs": db_invs})

        field_name_set = {f["field_name"] for f in parsed}

        # Map invariant_field_name → parent_field_name  (e.g. "S_MISES" → "S")
        inv_parent: dict[str, str] = {}
        for f in parsed:
            if not f["_comps"]:
                idx = f["field_name"].find("_")
                if idx > 0:
                    candidate = f["field_name"][:idx]
                    if candidate in field_name_set:
                        inv_parent[f["field_name"]] = candidate

        # Collect invariant suffixes per parent  (e.g. "S" → ["MISES"])
        # Non-MISES invariants are computed from components via numpy formulas and
        # not yet verified — hide them from the frontend until confirmed correct.
        _HIDDEN_INV_SUFFIXES = {"PRESS", "INV3", "MAX_PRINCIPAL", "MID_PRINCIPAL", "MIN_PRINCIPAL"}
        inv_suffixes: dict[str, list] = {}
        for inv_name, parent_name in sorted(inv_parent.items()):
            suffix = inv_name[len(parent_name) + 1:]
            if suffix in _HIDDEN_INV_SUFFIXES:
                continue
            inv_suffixes.setdefault(parent_name, []).append(suffix)

        result = []
        for f in parsed:
            if f["field_name"] in inv_parent:
                continue  # absorbed into parent's invariants list
            field_copy = {k: v for k, v in f.items() if k not in ("_comps", "_db_invs", "invariants")}
            field_copy["components"] = f["_comps"]
            synthetic_invs = list(inv_suffixes.get(f["field_name"], []))
            # Also surface on-the-fly-computable invariants from the DB column
            # (only for vector fields that have components — not for scalar fields)
            if f["_comps"]:
                for inv in f["_db_invs"]:
                    if inv in _ONTHEFLY_INVARIANTS and inv not in synthetic_invs:
                        synthetic_invs.append(inv)
            field_copy["invariants"] = synthetic_invs
            # positions is stored as a JSON string in the DB — deserialize it
            raw_pos = f.get("positions", "[]") or "[]"
            try:
                field_copy["positions"] = json.loads(raw_pos) if isinstance(raw_pos, str) else raw_pos
            except Exception:
                field_copy["positions"] = []
            result.append(field_copy)

        return result

    def adopt_null_result_group(self, result_group_name: str,
                                display_name: str, source_file: str = None) -> bool:
        """
        Tag every NULL result_group row in this manifest as result_group_name.
        Also creates a result_group_meta entry.
        Returns True if any rows were migrated or if result_group_meta was
        upserted (i.e. the group is at least partially adopted).

        Each table is handled independently so a partial-migration state
        (e.g. steps done, result_files not yet) can always be retried.
        """
        from datetime import datetime, timezone
        if not os.path.exists(self.db_path):
            return False
        try:
            with self._get_conn() as conn:
                now = datetime.now(timezone.utc).isoformat()
                any_migrated = False
                # Per-table: only update rows that are still NULL — skip tables
                # already fully migrated without blocking the others.
                for tbl in ("steps", "frames", "result_files", "result_blocks"):
                    try:
                        has_null = conn.execute(
                            f"SELECT 1 FROM {tbl} WHERE result_group IS NULL LIMIT 1"
                        ).fetchone()
                        if not has_null:
                            continue
                        cur = conn.execute(
                            f"UPDATE {tbl} SET result_group=?"
                            f" WHERE result_group IS NULL",
                            (result_group_name,)
                        )
                        if cur.rowcount > 0:
                            any_migrated = True
                    except Exception:
                        pass
                try:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS result_group_meta"
                        " (result_group TEXT PRIMARY KEY, display_name TEXT NOT NULL,"
                        "  source_file TEXT, consistency_check TEXT NOT NULL"
                        "  DEFAULT 'count-only', created_at TEXT NOT NULL)"
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO result_group_meta"
                        " (result_group, display_name, source_file,"
                        "  consistency_check, created_at)"
                        " VALUES (?,?,?,'count-only',?)",
                        (result_group_name, display_name, source_file, now)
                    )
                    any_migrated = True
                except Exception:
                    pass
            return any_migrated
        except Exception:
            return False

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

    def get_user_set_render_rows(self, set_name: str, instance_name: str):
        """
        Return render_rows (int32 ndarray) for a user set + instance, or None if not found.
        render_rows: triangle indices into the render buffer that belong to this set.
        """
        conn = self._get_conn()
        try:
            self._ensure_user_tables(conn)
            row = conn.execute(
                """
                SELECT usi.render_rows FROM user_set_instances usi
                JOIN user_sets us ON us.id = usi.us_id
                WHERE us.name = ? AND usi.instance_name = ?
                """,
                (set_name, instance_name),
            ).fetchone()
            if row is None or row["render_rows"] is None:
                return None
            return np.frombuffer(zlib.decompress(row["render_rows"]), dtype=np.int32).copy()
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

    def register_external_result(
        self,
        result_group: str,
        step_name: str,
        field_name: str,
        file_path: str,
        components: list,
        positions: list,
        instance_name: str,
        position: str,
        frames: list = None,
    ) -> None:
        """
        Insert/replace a result_files row and matching result_blocks row for an
        external (non-ODB) result written by ExternalResultWriter.
        """
        import json as _json
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO result_group_meta
                    (result_group, display_name, source_file, consistency_check, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (result_group, result_group, None, "count-only"),
            )
            if frames is not None:
                existing_step = conn.execute(
                    "SELECT step_number, procedure FROM steps WHERE result_group=? AND step_name=?",
                    (result_group, step_name),
                ).fetchone()
                if existing_step is not None:
                    step_number = int(existing_step["step_number"] or 0)
                    procedure = existing_step["procedure"] or "EXTERNAL"
                else:
                    next_step = conn.execute(
                        "SELECT COALESCE(MAX(step_number), -1) + 1 AS next_step FROM steps WHERE result_group=?",
                        (result_group,),
                    ).fetchone()
                    step_number = int(next_step["next_step"] or 0)
                    procedure = "EXTERNAL"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO steps
                        (result_group, step_name, step_number, procedure, num_frames)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (result_group, step_name, step_number, procedure, len(frames)),
                )
                conn.execute(
                    "DELETE FROM frames WHERE result_group=? AND step_name=?",
                    (result_group, step_name),
                )
                for frame in frames:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO frames
                            (result_group, step_name, frame_idx, frame_value, description)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            result_group,
                            step_name,
                            int(frame.get("frame_idx", 0)),
                            float(frame.get("frame_value", 0.0)),
                            str(frame.get("description") or f"Frame {int(frame.get('frame_idx', 0))}"),
                        ),
                    )
            conn.execute(
                """
                INSERT OR REPLACE INTO result_files
                    (result_group, step_name, field_name, file_path,
                     components, invariants, positions, has_section,
                     val_min, val_max, source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result_group, step_name, field_name, file_path,
                    _json.dumps(components), _json.dumps([]),
                    _json.dumps(positions), 0,
                    None, None, "external",
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO result_blocks
                    (result_group, step_name, field_name, instance_name,
                     position, elem_type, h5_path, label_path,
                     n_entities, n_ip, n_sp)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result_group, step_name, field_name, instance_name,
                    position, None, file_path, None,
                    0, 1, 1,
                ),
            )

    def delete_external_result(
        self,
        result_group: str,
        step_name: str,
        field_name: str,
    ) -> None:
        """Remove all manifest rows for one external field (overwrite preparation)."""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM result_blocks WHERE result_group=? AND step_name=? AND field_name=?",
                (result_group, step_name, field_name),
            )
            conn.execute(
                "DELETE FROM result_files WHERE result_group=? AND step_name=? AND field_name=? AND source='external'",
                (result_group, step_name, field_name),
            )

    # ── Simright adapter helpers ───────────────────────────────────────────────

    def list_steps(self):
        """Return all steps ordered by step_number, each as a dict."""
        try:
            with self._get_conn() as conn:
                try:
                    rows = conn.execute(
                        "SELECT step_name, step_number, procedure, num_frames, description"
                        " FROM steps ORDER BY step_number"
                    ).fetchall()
                except Exception:
                    # Old manifest.db without description column
                    rows = conn.execute(
                        "SELECT step_name, step_number, procedure, num_frames"
                        " FROM steps ORDER BY step_number"
                    ).fetchall()
                result = [dict(r) for r in rows]
                for r in result:
                    r.setdefault("description", None)
                return result
        except Exception:
            return []

    def list_frames(self, step_name: str):
        """Return lightweight frame list for a step (idx + description only)."""
        try:
            with self._get_conn() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT frame_idx, frame_value, description FROM frames "
                    "WHERE step_name=? ORDER BY frame_idx",
                    (step_name,),
                ).fetchall()]
        except Exception:
            return []

    def get_frame(self, step_name: str, frame_idx: int):
        """Return full metadata for a single frame, or None if not found."""
        try:
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM frames WHERE step_name=? AND frame_idx=?",
                    (step_name, frame_idx),
                ).fetchone()
                return dict(row) if row else None
        except Exception:
            return None

    def list_result_files(self, step_name: str = None):
        """Return result_files rows, optionally filtered by step."""
        try:
            with self._get_conn() as conn:
                if step_name:
                    return [dict(r) for r in conn.execute(
                        "SELECT step_name, field_name, components, positions FROM result_files "
                        "WHERE step_name=? ORDER BY field_name",
                        (step_name,),
                    ).fetchall()]
                return [dict(r) for r in conn.execute(
                    "SELECT step_name, field_name, components, positions FROM result_files "
                    "ORDER BY field_name"
                ).fetchall()]
        except Exception:
            return []

    def list_instances(self):
        """Return all instances as dicts (includes bbox_min/bbox_max JSON strings)."""
        try:
            with self._get_conn() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT rowid, instance_name, part_name, geom_path, bbox_min, bbox_max "
                    "FROM instances ORDER BY rowid"
                ).fetchall()]
        except Exception:
            return []

    def get_instances_by_part_name(self, part_name: str) -> list:
        """Return instance_name list for all instances of the given part."""
        try:
            with self._get_conn() as conn:
                return [
                    r["instance_name"]
                    for r in conn.execute(
                        "SELECT instance_name FROM instances WHERE part_name=? ORDER BY rowid",
                        (part_name,),
                    ).fetchall()
                ]
        except Exception:
            return []

    def list_node_sets(self):
        """Return all node sets."""
        try:
            with self._get_conn() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT set_name, instance_name FROM node_sets"
                ).fetchall()]
        except Exception:
            return []

    def get_element_set_labels(self, set_name: str, instance_name: str):
        """
        Return sorted int32 label array for a named element set, or None if not found.

        h5_path formats stored by different parsers:
          INP (exporter.py): "l1/sets/sets.h5"
              → dataset = "element_sets/{instance}/{set_name}"
          ODB (l1_pack.py):  "l1/assembly.h5:assembly_sets/SET/INST/elem_labels"
              → split on ':' to get file and dataset path
        """
        import h5py as _h5py
        try:
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT h5_path FROM element_sets"
                    " WHERE set_name=? AND instance_name=?",
                    (set_name, instance_name),
                ).fetchone()
        except Exception:
            return None
        if row is None:
            return None

        h5_path_raw = row["h5_path"]
        if ":" in h5_path_raw:
            file_rel, ds_path = h5_path_raw.split(":", 1)
        else:
            file_rel = h5_path_raw
            ds_path = "element_sets/{}/{}".format(instance_name, set_name)

        abs_path = os.path.join(os.path.dirname(self.db_path), file_rel)
        if not os.path.exists(abs_path):
            return None
        try:
            with _h5py.File(abs_path, "r") as f:
                if ds_path not in f:
                    return None
                return f[ds_path][:].astype(np.int32)
        except Exception:
            return None

    def list_element_sets(self):
        """Return all element sets."""
        try:
            with self._get_conn() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT set_name, instance_name FROM element_sets"
                ).fetchall()]
        except Exception:
            return []

    def list_result_blocks(self, step_name: str, field_name: str):
        """Return result_blocks for (step, field) — one row per (instance, position, etype)."""
        try:
            with self._get_conn() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT instance_name, position, elem_type, h5_path FROM result_blocks "
                    "WHERE step_name=? AND field_name=? ORDER BY instance_name",
                    (step_name, field_name),
                ).fetchall()]
        except Exception:
            return []

    def get_display_names(self, instance: str, scheme: str) -> dict:
        """Return {legend_key: display_name} for the given instance + scheme."""
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT legend_key, display_name FROM display_names "
                    "WHERE instance=? AND scheme=?",
                    (instance, scheme),
                ).fetchall()
                return {r["legend_key"]: r["display_name"] for r in rows}
        except Exception:
            return {}

    def set_display_names(self, instance: str, scheme: str, names: dict) -> None:
        """Upsert {legend_key: display_name} entries for the given instance + scheme."""
        with self._get_conn() as conn:
            for key, val in names.items():
                conn.execute(
                    """INSERT INTO display_names (instance, scheme, legend_key, display_name)
                       VALUES (?,?,?,?)
                       ON CONFLICT(instance, scheme, legend_key)
                       DO UPDATE SET display_name=excluded.display_name""",
                    (instance, scheme, key, val),
                )
            conn.commit()

    def get_legend_overrides(self, instance: str, scheme: str) -> dict:
        """Return {legend_key: {display_name?, color_r?, color_g?, color_b?}} for the given instance + scheme."""
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT legend_key, display_name, color_r, color_g, color_b"
                    " FROM display_names WHERE instance=? AND scheme=?",
                    (instance, scheme),
                ).fetchall()
            result = {}
            for r in rows:
                entry = {}
                if r["display_name"] is not None:
                    entry["display_name"] = r["display_name"]
                if r["color_r"] is not None:
                    entry["color_r"] = r["color_r"]
                    entry["color_g"] = r["color_g"]
                    entry["color_b"] = r["color_b"]
                if entry:
                    result[r["legend_key"]] = entry
            return result
        except Exception:
            return {}

    def set_legend_overrides(self, instance: str, scheme: str, entries: list) -> None:
        """
        Upsert legend overrides. entries: list of dicts with keys:
          legend_key (required), display_name (str | None), color_r/g/b (float | None).
        Pass None for display_name / color to clear that override.
        """
        with self._get_conn() as conn:
            for e in entries:
                key = e["legend_key"]
                dn  = e.get("display_name")
                cr  = e.get("color_r")
                cg  = e.get("color_g")
                cb  = e.get("color_b")
                conn.execute(
                    """INSERT INTO display_names
                           (instance, scheme, legend_key, display_name, color_r, color_g, color_b)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(instance, scheme, legend_key)
                       DO UPDATE SET
                           display_name = excluded.display_name,
                           color_r      = excluded.color_r,
                           color_g      = excluded.color_g,
                           color_b      = excluded.color_b""",
                    (instance, scheme, key, dn, cr, cg, cb),
                )
            conn.commit()
