"""Opt-in live comparison on disposable tables, never the business tables.

Run locally with RUN_MYSQL_DM_INTEGRATION=1 and DM_PASSWORD set.
"""

import json
import os
from uuid import uuid4

import pytest


pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_DM_INTEGRATION") != "1",
                                reason="Explicit opt-in required for live database comparison")


def test_mysql_dm_insert_upsert_batch_nullable_keys_identity_and_rollback():
    from config import _SERVICE_CONFIG
    from db_dameng import DamengPool
    import mysql.connector
    pytest.importorskip("dmPython")

    table = "odb_verify_" + uuid4().hex[:12]
    ddl = f"""CREATE TABLE IF NOT EXISTS {table} (
        id BIGINT NOT NULL AUTO_INCREMENT,
        pid BIGINT NOT NULL,
        code VARCHAR(100) NULL,
        payload JSON NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY(id),
        UNIQUE KEY uk_pid_code(pid,code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
    mysql_config = {key: os.getenv("DB_" + key.upper(), _SERVICE_CONFIG.get("DB_" + key.upper(), default))
        for key, default in {"host": "localhost", "port": 3306, "user": "root", "password": "",
                             "database": "db_simu_real_test"}.items()}
    mysql_config["port"] = int(mysql_config["port"])
    mysql = mysql.connector.connect(**mysql_config)
    config = {"host": os.getenv("DM_HOST", _SERVICE_CONFIG.get("DM_HOST", "localhost")),
              "port": int(os.getenv("DM_PORT", _SERVICE_CONFIG.get("DM_PORT", 5236))),
              "user": os.getenv("DM_USER", _SERVICE_CONFIG.get("DM_USER", "SYSDBA")),
              "password": os.getenv("DM_PASSWORD", _SERVICE_CONFIG.get("DM_PASSWORD", "")),
              "database": os.getenv("DM_SCHEMA", _SERVICE_CONFIG.get("DM_SCHEMA", "DB_SIMU_REAL_TEST"))}
    dm = None
    try:
        dm = DamengPool(config, [ddl]).get_connection()
        for conn in (mysql, dm):
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

        insert = f"INSERT INTO {table}(pid,code,payload) VALUES (%s,%s,%s)"
        upsert = insert + " ON DUPLICATE KEY UPDATE payload=VALUES(payload)"
        payload = json.dumps({"text": "中文" * 5000}, ensure_ascii=False)
        snapshots = []
        for conn in (mysql, dm):
            with conn.cursor() as cur:
                cur.execute(insert, (1, "a", '{}'))
                assert isinstance(cur.lastrowid, int) and cur.lastrowid > 0
                cur.execute(f"SELECT updated_at FROM {table} WHERE pid=1")
                initial_time = cur.fetchone()[0]
                cur.execute(upsert, (1, "a", '{}'))
                cur.execute(f"SELECT updated_at FROM {table} WHERE pid=1")
                assert cur.fetchone()[0] == initial_time
                cur.execute(upsert, (1, "a", payload))
                cur.executemany(upsert, [(2, None, '{}'), (2, None, '{}')])
                cur.executemany(f"INSERT INTO {table}(id,pid,code,payload) VALUES(%s,%s,%s,%s)",
                                [(101, 3, "c", '{}'), (102, 3, "d", '{}')])
                conn.commit()
                cur.execute(insert, (9, "rollback", '{}'))
                conn.rollback()
                with pytest.raises(Exception):
                    cur.execute(insert, (9, "invalid-json", '{broken'))
                conn.rollback()
                cur.execute(f"SELECT pid,code,payload FROM {table} ORDER BY pid,code")
                rows = cur.fetchall()
                snapshots.append([(int(pid), code, json.loads(value)) for pid, code, value in rows])
        assert snapshots[0] == snapshots[1]
        assert len(snapshots[0]) == 5
    finally:
        for conn in (mysql, dm):
            if conn is not None:
                try:
                    with conn.cursor() as cur:
                        cur.execute(f"DROP TABLE IF EXISTS {table}")
                    conn.commit()
                finally:
                    conn.close()
