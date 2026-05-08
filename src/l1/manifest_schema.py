"""
manifest_schema.py — 共用 manifest.db DDL。

l1_pack.py 和 inp/exporter.py 都 import 这里，保证两条路径建出来的 schema 一致。
"""

MANIFEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    instance_name  TEXT PRIMARY KEY,
    part_name      TEXT,
    geom_path      TEXT,
    highorder_path TEXT,
    node_count     INTEGER,
    elem_count     INTEGER,
    bbox_min       TEXT,
    bbox_max       TEXT
);
CREATE TABLE IF NOT EXISTS element_type_dist (
    instance_name  TEXT,
    elem_type      TEXT,
    count          INTEGER,
    has_midnodes   INTEGER,
    n_corner_nodes INTEGER,
    n_faces        INTEGER,
    PRIMARY KEY (instance_name, elem_type)
);
CREATE TABLE IF NOT EXISTS steps (
    result_group TEXT,
    step_name    TEXT,
    step_number  INTEGER,
    procedure    TEXT,
    num_frames   INTEGER,
    description  TEXT,
    PRIMARY KEY (result_group, step_name)
);
CREATE TABLE IF NOT EXISTS frames (
    result_group       TEXT,
    step_name          TEXT,
    frame_idx          INTEGER,
    frame_value        REAL,
    description        TEXT,
    domain             TEXT,
    frequency          REAL,
    mode_number        INTEGER,
    increment_number   INTEGER,
    is_imaginary       INTEGER,
    frame_id           INTEGER,
    cyclic_mode_number INTEGER,
    load_case          TEXT,
    PRIMARY KEY (result_group, step_name, frame_idx)
);
CREATE TABLE IF NOT EXISTS result_files (
    result_group TEXT,
    step_name    TEXT,
    field_name   TEXT,
    file_path    TEXT,
    components   TEXT,
    invariants   TEXT,
    positions    TEXT,
    has_section  INTEGER,
    val_min      REAL,
    val_max      REAL,
    source       TEXT NOT NULL DEFAULT 'odb',
    PRIMARY KEY (result_group, step_name, field_name)
);
CREATE TABLE IF NOT EXISTS result_blocks (
    result_group  TEXT,
    step_name     TEXT,
    field_name    TEXT,
    instance_name TEXT,
    position      TEXT,
    elem_type     TEXT,
    h5_path       TEXT,
    label_path    TEXT,
    n_entities    INTEGER,
    n_ip          INTEGER,
    n_sp          INTEGER,
    PRIMARY KEY (result_group, step_name, field_name, instance_name, position, elem_type)
);
CREATE TABLE IF NOT EXISTS node_sets (
    set_name      TEXT,
    set_scope     TEXT,
    instance_name TEXT,
    h5_path       TEXT,
    node_count    INTEGER,
    PRIMARY KEY (set_name, instance_name)
);
CREATE TABLE IF NOT EXISTS element_sets (
    set_name      TEXT,
    set_scope     TEXT,
    instance_name TEXT,
    h5_path       TEXT,
    elem_count    INTEGER,
    PRIMARY KEY (set_name, instance_name)
);
CREATE TABLE IF NOT EXISTS result_group_meta (
    result_group       TEXT PRIMARY KEY,
    display_name       TEXT NOT NULL,
    source_file        TEXT,
    consistency_check  TEXT NOT NULL DEFAULT 'count-only',
    created_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS display_names (
    instance     TEXT NOT NULL,
    scheme       TEXT NOT NULL,
    legend_key   TEXT NOT NULL,
    display_name TEXT NOT NULL,
    PRIMARY KEY (instance, scheme, legend_key)
);
"""
# user_sets / user_set_instances tables are NOT created here.
# They are created on-demand by ManifestRepo._ensure_user_tables() when the
# first bbox selection is saved, so the schema stays in one place.
