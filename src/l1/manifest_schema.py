"""
manifest_schema.py — 共用 manifest.db DDL。

l1_pack.py 和 inp/exporter.py 都 import 这里，保证两条路径建出来的 schema 一致。
"""


def canon_instance(name):
    """Instance 名规范化：统一大写。

    Abaqus 的 instance / set / elset 名都是大小写无关的，但不同来源写法不一：
    INP 文件保留用户原始大小写（常见小写），ODB 默认全大写。两侧名字若按字面
    比较就会 mismatch（典型症状：abaqus_dump 报 "instance list mismatch"，
    或 L2 按 manifest 名字去 assembly.h5 查 transform 时 KeyError）。

    约定：凡是 instance 名进入流水线（写 manifest / 写 assembly.h5 group /
    建 result_block key）的源头，都过这个函数归一化为大写，下游一路带着大写走，
    保证 manifest.instance_name 与 assembly.h5/sets.h5 的 group 名逐字一致。

    Python 2/3 通用（abaqus_dump.py 在 Abaqus Python 2.7 下另有本地副本 _canon_inst）。
    """
    if name is None:
        return name
    return name.upper()


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
    nlgeom       INTEGER,
    total_time   REAL,   -- step 开始时的累计分析总时间（odb step.totalTime；旧数据为 NULL）
    time_period  REAL,   -- 该 step 的时长（odb step.timePeriod；旧数据为 NULL）
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
    is_internal   INTEGER NOT NULL DEFAULT 0,  -- 1 = Abaqus-generated (_PickedSetNN); 0 = user-named
    PRIMARY KEY (set_name, instance_name)
);
CREATE TABLE IF NOT EXISTS result_group_meta (
    result_group       TEXT PRIMARY KEY,
    display_name       TEXT NOT NULL,
    source_file        TEXT,
    consistency_check  TEXT NOT NULL DEFAULT 'count-only',
    created_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS l1_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS display_names (
    instance     TEXT NOT NULL,
    scheme       TEXT NOT NULL,
    legend_key   TEXT NOT NULL,
    display_name TEXT,
    color_r      REAL,
    color_g      REAL,
    color_b      REAL,
    PRIMARY KEY (instance, scheme, legend_key)
);
"""
# user_sets / user_set_instances tables are NOT created here.
# They are created on-demand by ManifestRepo._ensure_user_tables() when the
# first bbox selection is saved, so the schema stays in one place.
