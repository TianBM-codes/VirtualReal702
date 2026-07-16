# -*- coding: utf-8 -*-
"""
migrate_result_blocks 迁移测试。

场景来自线上真实 bug（2026-07-16，project 202607161656）：
壳单元结果按截面点分块（S4R 有 sp1/sp5 两块），旧表主键没有 sp_num，
两块以"重复行"共存（result_group 为 NULL 时 SQLite 唯一索引把 NULL 视为
互不相等）。job_runner 的 result_group 打标 UPDATE 撞主键整条失败且被
静默吞掉，导致 L3 所有走 result_blocks 的接口（/fields、node-table、
node-time-value、result-catalog）在该 result_group 下查不到任何行。
"""
import sqlite3

import pytest

from src.l1.manifest_schema import migrate_result_blocks

OLD_DDL = """
CREATE TABLE result_blocks (
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
"""


@pytest.fixture
def old_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(OLD_DDL)
    # NODAL 块（无 elem_type、无 sp）
    conn.execute(
        "INSERT INTO result_blocks VALUES (NULL,'sag','U','PART-1-1','NODAL',NULL,"
        " '/NODAL/PART-1-1','/NODAL/PART-1-1/labels',100,NULL,NULL)")
    # 壳单元两个截面点块——旧主键下的"重复行"
    for sp in (1, 5):
        conn.execute(
            "INSERT INTO result_blocks VALUES (NULL,'sag','S','PART-1-1',"
            " 'ELEMENT_NODAL','S4R',"
            " '/ELEMENT_NODAL/PART-1-1/S4R/sp{0}',"
            " '/ELEMENT_NODAL/PART-1-1/S4R/sp{0}/labels',50,NULL,2)".format(sp))
    # 实体单元块（elem_type 有值、无 sp）
    conn.execute(
        "INSERT INTO result_blocks VALUES (NULL,'sag','S','PART-1-1',"
        " 'INTEGRATION_POINT','C3D4',"
        " '/INTEGRATION_POINT/PART-1-1/C3D4',"
        " '/INTEGRATION_POINT/PART-1-1/C3D4/labels',200,1,NULL)")
    conn.commit()
    yield conn
    conn.close()


def test_migration_extracts_sp_num_and_keeps_all_rows(old_conn):
    assert migrate_result_blocks(old_conn) is True

    rows = old_conn.execute(
        "SELECT elem_type, sp_num, h5_path FROM result_blocks"
        " WHERE field_name='S' AND position='ELEMENT_NODAL' ORDER BY sp_num"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("S4R", 1), ("S4R", 5)]

    # 无 sp 的块 sp_num=-1
    assert old_conn.execute(
        "SELECT sp_num FROM result_blocks WHERE position='NODAL'"
    ).fetchone()[0] == -1
    assert old_conn.execute(
        "SELECT sp_num FROM result_blocks WHERE elem_type='C3D4'"
    ).fetchone()[0] == -1

    assert old_conn.execute(
        "SELECT COUNT(*) FROM result_blocks").fetchone()[0] == 4


def test_tagging_update_succeeds_after_migration(old_conn):
    """迁移后 result_group 打标不再撞主键（线上 bug 的直接症状）。"""
    migrate_result_blocks(old_conn)
    old_conn.execute(
        "UPDATE result_blocks SET result_group='default_result'"
        " WHERE result_group IS NULL")
    assert old_conn.execute(
        "SELECT COUNT(*) FROM result_blocks WHERE result_group='default_result'"
    ).fetchone()[0] == 4


def test_tagging_update_fails_without_migration(old_conn):
    """反向验证：不迁移直接打标必然撞主键——这就是当年静默失败的原因。"""
    with pytest.raises(sqlite3.IntegrityError):
        old_conn.execute(
            "UPDATE result_blocks SET result_group='default_result'"
            " WHERE result_group IS NULL")


def test_migration_is_idempotent(old_conn):
    assert migrate_result_blocks(old_conn) is True
    assert migrate_result_blocks(old_conn) is False
    assert old_conn.execute(
        "SELECT COUNT(*) FROM result_blocks").fetchone()[0] == 4


def test_migration_noop_when_table_missing():
    conn = sqlite3.connect(":memory:")
    assert migrate_result_blocks(conn) is False
    conn.close()
