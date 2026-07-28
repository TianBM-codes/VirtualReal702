"""node-time-value 服务测试：按时间查节点值（prev/next/interp + 全局/局部时间）。"""
import sqlite3
from pathlib import Path

import h5py
import numpy as np
import pytest

from src.l1.manifest_schema import MANIFEST_SCHEMA
from src.l3.core.errors import NotFoundError, ValidationError
from src.l3.services.node_time_value_service import get_node_time_value

INSTANCE = "PART-1-1"


def _setup_manifest(workspace: Path, steps: list, fields: dict) -> None:
    """
    steps:  [(step_name, step_number, procedure, [frame_value, ...]), ...]
            或带精确时间的 6 元组 (..., total_time, time_period)
    fields: {field_name: {"components": [...], "invariants": [...]}}
            每个 field 在所有 step 注册 NODAL block。
    """
    with sqlite3.connect(workspace / "manifest.db") as conn:
        conn.executescript(MANIFEST_SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO instances (instance_name, part_name, geom_path) VALUES (?,?,?)",
            (INSTANCE, "PART-1", f"l1/geometry/{INSTANCE}.h5"),
        )
        for entry in steps:
            step_name, step_number, procedure, frame_values = entry[:4]
            total_time, time_period = (entry[4], entry[5]) if len(entry) > 4 else (None, None)
            conn.execute(
                "INSERT INTO steps (result_group, step_name, step_number, procedure,"
                " num_frames, total_time, time_period) VALUES (NULL,?,?,?,?,?,?)",
                (step_name, step_number, procedure, len(frame_values),
                 total_time, time_period),
            )
            for fi, fv in enumerate(frame_values):
                conn.execute(
                    "INSERT INTO frames (result_group, step_name, frame_idx, frame_value)"
                    " VALUES (NULL,?,?,?)",
                    (step_name, fi, fv),
                )
            for fname, meta in fields.items():
                conn.execute(
                    "INSERT INTO result_files (result_group, step_name, field_name,"
                    " file_path, components, invariants, positions)"
                    " VALUES (NULL,?,?,?,?,?,?)",
                    (step_name, fname, "", str(list(meta["components"])).replace("'", '"'),
                     str(list(meta.get("invariants", []))).replace("'", '"'), '["NODAL"]'),
                )
                conn.execute(
                    "INSERT INTO result_blocks (result_group, step_name, field_name,"
                    " instance_name, position) VALUES (NULL,?,?,?,'NODAL')",
                    (step_name, fname, INSTANCE),
                )


def _write_result(workspace: Path, step: str, field: str,
                  labels: np.ndarray, data: np.ndarray) -> None:
    path = workspace / "l1" / "results" / f"{step}__{field}.h5"
    with h5py.File(path, "w") as f:
        grp = f.create_group("NODAL").create_group(INSTANCE)
        grp.create_dataset("data", data=np.asarray(data, dtype=np.float32))
        grp.create_dataset("labels", data=np.asarray(labels, dtype=np.int32))


@pytest.fixture
def single_step_ws(workspace, make_registry):
    """Step-1 (STATIC)，帧时间 [0.0, 0.5, 1.0]，场 U (U1,U2,U3)。"""
    (workspace / "manifest.db").unlink()
    _setup_manifest(
        workspace,
        steps=[("Step-1", 1, "STATIC", [0.0, 0.5, 1.0])],
        fields={"U": {"components": ["U1", "U2", "U3"], "invariants": ["MAGNITUDE"]}},
    )
    labels = np.array([10, 20], dtype=np.int32)
    # 帧 f、节点行 r：U = (f*10 + r, 0, 0)，便于核对
    data = np.array(
        [[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
         [[10.0, 0.0, 0.0], [11.0, 0.0, 0.0]],
         [[20.0, 0.0, 0.0], [21.0, 0.0, 0.0]]],
        dtype=np.float32,
    )
    _write_result(workspace, "Step-1", "U", labels, data)
    return make_registry(workspace)


def test_exact_time_hits_frame(single_step_ws):
    out = get_node_time_value(
        single_step_ws, "odb", INSTANCE, "U", [10, 20],
        time=0.5, step="Step-1", time_match="interp",
    )
    assert out["resolved_mode"] == "exact"
    assert out["frames_used"] == [
        {"step": "Step-1", "frame_idx": 1, "step_time": 0.5, "global_time": None, "weight": 1.0}
    ]
    assert out["components"] == ["U1", "U2", "U3"]
    assert out["invariants"] == ["MAGNITUDE"]  # 现算不变量
    node10 = out["nodes"][0]
    assert node10["found"] and node10["values"]["U1"] == pytest.approx(10.0)
    assert node10["values"]["MAGNITUDE"] == pytest.approx(10.0)


def test_interp_between_frames(single_step_ws):
    out = get_node_time_value(
        single_step_ws, "odb", INSTANCE, "U", [10],
        time=0.75, step="Step-1", time_match="interp",
    )
    assert out["resolved_mode"] == "interp"
    weights = [f["weight"] for f in out["frames_used"]]
    assert weights == [pytest.approx(0.5), pytest.approx(0.5)]
    assert out["nodes"][0]["values"]["U1"] == pytest.approx(15.0)


def test_prev_and_next_pick_neighbouring_frames(single_step_ws):
    prev = get_node_time_value(
        single_step_ws, "odb", INSTANCE, "U", [10],
        time=0.75, step="Step-1", time_match="prev",
    )
    assert prev["resolved_mode"] == "prev"
    assert prev["frames_used"][0]["frame_idx"] == 1
    assert prev["nodes"][0]["values"]["U1"] == pytest.approx(10.0)

    nxt = get_node_time_value(
        single_step_ws, "odb", INSTANCE, "U", [10],
        time=0.75, step="Step-1", time_match="next",
    )
    assert nxt["resolved_mode"] == "next"
    assert nxt["frames_used"][0]["frame_idx"] == 2
    assert nxt["nodes"][0]["values"]["U1"] == pytest.approx(20.0)


def test_out_of_range_rules(single_step_ws):
    # 早于第一帧：prev / interp 报 400，next 取第一帧
    with pytest.raises(ValidationError):
        get_node_time_value(single_step_ws, "odb", INSTANCE, "U", [10],
                            time=-0.5, step="Step-1", time_match="prev")
    with pytest.raises(ValidationError):
        get_node_time_value(single_step_ws, "odb", INSTANCE, "U", [10],
                            time=-0.5, step="Step-1", time_match="interp")
    nxt = get_node_time_value(single_step_ws, "odb", INSTANCE, "U", [10],
                              time=-0.5, step="Step-1", time_match="next")
    assert nxt["frames_used"][0]["frame_idx"] == 0

    # 晚于最后一帧：next / interp 报 400，prev 取最后一帧
    with pytest.raises(ValidationError):
        get_node_time_value(single_step_ws, "odb", INSTANCE, "U", [10],
                            time=2.0, step="Step-1", time_match="next")
    prev = get_node_time_value(single_step_ws, "odb", INSTANCE, "U", [10],
                               time=2.0, step="Step-1", time_match="prev")
    assert prev["frames_used"][0]["frame_idx"] == 2


def test_missing_node_reports_found_false(single_step_ws):
    out = get_node_time_value(
        single_step_ws, "odb", INSTANCE, "U", [10, 999],
        time=0.5, step="Step-1", time_match="interp",
    )
    assert out["nodes"][0]["found"] is True
    assert out["nodes"][1] == {"label": 999, "found": False, "values": None}


def test_invalid_time_match_rejected(single_step_ws):
    with pytest.raises(ValidationError):
        get_node_time_value(single_step_ws, "odb", INSTANCE, "U", [10],
                            time=0.5, step="Step-1", time_match="nearest")


def test_unknown_step_rejected(single_step_ws):
    with pytest.raises(NotFoundError):
        get_node_time_value(single_step_ws, "odb", INSTANCE, "U", [10],
                            time=0.5, step="Step-99", time_match="interp")


@pytest.fixture
def two_step_ws(workspace, make_registry):
    """
    全局时间轴：Step-1 (STATIC, 帧 [0.0, 1.0]) + Step-2 (STATIC, 帧 [0.0, 1.0])
    → 全局时间 [0.0, 1.0, 1.0, 2.0]。场 S 为标量+存储式不变量 S_MISES。
    """
    (workspace / "manifest.db").unlink()
    _setup_manifest(
        workspace,
        steps=[
            ("Step-1", 1, "STATIC", [0.0, 1.0]),
            ("Step-2", 2, "STATIC", [0.0, 1.0]),
            ("Freq-1", 3, "FREQUENCY", [100.0, 200.0]),  # 不应进入全局时间轴
        ],
        fields={
            "S": {"components": ["S11", "S22"], "invariants": ["MISES"]},
            "S_MISES": {"components": [], "invariants": []},
        },
    )
    labels = np.array([10], dtype=np.int32)
    _write_result(workspace, "Step-1", "S", labels,
                  np.array([[[0.0, 0.0]], [[10.0, 0.0]]], dtype=np.float32))
    _write_result(workspace, "Step-2", "S", labels,
                  np.array([[[10.0, 0.0]], [[30.0, 0.0]]], dtype=np.float32))
    _write_result(workspace, "Freq-1", "S", labels,
                  np.array([[[77.0, 0.0]], [[88.0, 0.0]]], dtype=np.float32))
    _write_result(workspace, "Step-1", "S_MISES", labels,
                  np.array([[[0.0]], [[100.0]]], dtype=np.float32))
    _write_result(workspace, "Step-2", "S_MISES", labels,
                  np.array([[[100.0]], [[300.0]]], dtype=np.float32))
    return make_registry(workspace)


def test_global_time_spans_steps_and_skips_frequency(two_step_ws):
    # 全局 1.5 = Step-2 局部 0.5，两帧插值
    out = get_node_time_value(
        two_step_ws, "odb", INSTANCE, "S", [10],
        time=1.5, step=None, time_match="interp",
    )
    assert out["time_mode"] == "global"
    assert out["resolved_mode"] == "interp"
    assert all(f["step"] == "Step-2" for f in out["frames_used"])
    assert out["nodes"][0]["values"]["S11"] == pytest.approx(20.0)
    # 存储式不变量同样按帧插值
    assert out["invariants"] == ["MISES"]
    assert out["nodes"][0]["values"]["MISES"] == pytest.approx(200.0)
    # 全局时间范围 [0, 2]，FREQUENCY step 的 100/200 没被当成时间
    assert out["time_range"] == {"min": 0.0, "max": 2.0}


def test_global_time_prefers_exact_step_times(workspace, make_registry):
    """
    Step-1 中途中止：帧只到 0.7,但精确 time_period=1.0 → Step-2 起点应是 1.0。
    若走推算(末帧累加)Step-2 起点会错成 0.7。
    """
    (workspace / "manifest.db").unlink()
    _setup_manifest(
        workspace,
        steps=[
            ("Step-1", 1, "STATIC", [0.0, 0.7], 0.0, 1.0),
            ("Step-2", 2, "STATIC", [0.0, 1.0], 1.0, 1.0),
        ],
        fields={"U": {"components": ["U1"], "invariants": []}},
    )
    labels = np.array([10], dtype=np.int32)
    _write_result(workspace, "Step-1", "U", labels,
                  np.array([[[0.0]], [[7.0]]], dtype=np.float32))
    _write_result(workspace, "Step-2", "U", labels,
                  np.array([[[10.0]], [[30.0]]], dtype=np.float32))
    registry = make_registry(workspace)

    # 全局 1.5 = Step-2 局部 0.5(精确口径);推算口径会当成 Step-2 局部 0.8
    out = get_node_time_value(registry, "odb", INSTANCE, "U", [10],
                              time=1.5, step=None, time_match="interp")
    assert all(f["step"] == "Step-2" for f in out["frames_used"])
    assert out["nodes"][0]["values"]["U1"] == pytest.approx(20.0)
    # 时间轴终点 = Step-2 起点 1.0 + 末帧 1.0
    assert out["time_range"] == {"min": 0.0, "max": 2.0}

    # 全局 0.85 落在 Step-1 末帧(0.7)和 Step-2 首帧(1.0)之间 → 跨 step 插值
    out2 = get_node_time_value(registry, "odb", INSTANCE, "U", [10],
                               time=0.85, step=None, time_match="interp")
    assert [f["step"] for f in out2["frames_used"]] == ["Step-1", "Step-2"]
    assert out2["nodes"][0]["values"]["U1"] == pytest.approx(8.5)  # 7→10 中点


def test_global_time_on_step_boundary_degenerates_to_exact(two_step_ws):
    # 全局 1.0 同时是 Step-1 末帧和 Step-2 首帧 → 命中而非插值
    out = get_node_time_value(
        two_step_ws, "odb", INSTANCE, "S", [10],
        time=1.0, step=None, time_match="interp",
    )
    assert out["resolved_mode"] == "exact"
    assert len(out["frames_used"]) == 1
    assert out["nodes"][0]["values"]["S11"] == pytest.approx(10.0)


# ── FREQUENCY 步：frame_value 是模态阶次/频率，不是时间 ────────────────────────

@pytest.fixture
def frequency_step_ws(workspace, make_registry):
    """Modal (FREQUENCY)，frame_value [0, 1, 2]（基态 + 两阶模态），场 U。"""
    (workspace / "manifest.db").unlink()
    _setup_manifest(
        workspace,
        steps=[("Modal", 1, "FREQUENCY", [0.0, 1.0, 2.0])],
        fields={"U": {"components": ["U1", "U2", "U3"], "invariants": []}},
    )
    labels = np.array([10], dtype=np.int32)
    data = np.array(
        [[[0.0, 0.0, 0.0]], [[10.0, 0.0, 0.0]], [[20.0, 0.0, 0.0]]],
        dtype=np.float32,
    )
    _write_result(workspace, "Modal", "U", labels, data)
    return make_registry(workspace)


def test_frequency_step_rejects_interp_between_modes(frequency_step_ws):
    """两个模态振型之间插值无物理意义，落在两帧之间的 interp 必须报 400。"""
    with pytest.raises(ValidationError, match="FREQUENCY"):
        get_node_time_value(frequency_step_ws, "odb", INSTANCE, "U", [10],
                            time=0.5, step="Modal", time_match="interp")


def test_frequency_step_allows_exact_prev_next(frequency_step_ws):
    # 精确命中某阶 → interp 退化为 exact，放行
    out = get_node_time_value(frequency_step_ws, "odb", INSTANCE, "U", [10],
                              time=1.0, step="Modal", time_match="interp")
    assert out["resolved_mode"] == "exact"
    assert out["nodes"][0]["values"]["U1"] == pytest.approx(10.0)

    # prev/next 按阶次轴取最近一帧，有意义，放行
    prev = get_node_time_value(frequency_step_ws, "odb", INSTANCE, "U", [10],
                               time=1.5, step="Modal", time_match="prev")
    assert prev["frames_used"][0]["frame_idx"] == 1
    nxt = get_node_time_value(frequency_step_ws, "odb", INSTANCE, "U", [10],
                              time=1.5, step="Modal", time_match="next")
    assert nxt["frames_used"][0]["frame_idx"] == 2


# ── 路由层：业务错误一律 HTTP 200 + 信封里的 code/message ──────────────────────

def _client(monkeypatch, registry):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.l3.api.routes import node_table

    monkeypatch.setattr(node_table, "registry", registry)
    app = FastAPI()
    app.include_router(node_table.router)
    return TestClient(app)


def _post(client, **overrides):
    body = {"instance": INSTANCE, "field": "U", "node_labels": [10],
            "time": 0.5, "step": "Step-1", "time_match": "interp"}
    body.update(overrides)
    return client.post("/api/odb/odb/results/node-time-value", json=body)


def test_route_returns_200_on_success(monkeypatch, single_step_ws):
    resp = _post(_client(monkeypatch, single_step_ws))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200 and body["message"] == ""
    assert body["data"]["nodes"][0]["values"]["U1"] == pytest.approx(10.0)


def test_route_returns_200_when_time_out_of_range(monkeypatch, single_step_ws):
    """原来是 HTTP 400；现在 HTTP 层永远 200，错误在信封里。"""
    resp = _post(_client(monkeypatch, single_step_ws), time=99.0)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 400
    assert "超出" in body["message"]
    # data 与成功时同构，取不到值 → found=false / values=null
    assert body["data"]["nodes"] == [{"label": 10, "found": False, "values": None}]
    assert body["data"]["resolved_mode"] is None
    assert body["data"]["frames_used"] == []


def test_route_returns_200_on_frequency_interp(monkeypatch, frequency_step_ws):
    resp = _post(_client(monkeypatch, frequency_step_ws), step="Modal")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 400
    assert "FREQUENCY" in body["message"] and "插值" in body["message"]
    assert body["data"]["nodes"][0]["values"] is None


def test_route_returns_200_on_unknown_step(monkeypatch, single_step_ws):
    """NotFound 类错误同样走 200，code=404。"""
    resp = _post(_client(monkeypatch, single_step_ws), step="Step-99")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 404
    assert "Step-99" in body["message"]
    assert body["data"]["nodes"][0]["values"] is None
