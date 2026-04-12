import asyncio
import json

import numpy as np
import pytest

from src.l3.services.raw_result_service import raw_values_to_json_payload


def test_raw_values_to_json_payload_for_nodal():
    payload = raw_values_to_json_payload(
        sections=[
            ("node_labels", np.array([1, 2], dtype=np.int32)),
            ("values", np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)),
        ],
        components=["U1", "U2"],
        etype_groups=[],
        position="NODAL",
        odb_id="odb-1",
        instance="PART-1-1",
        step="Step-1",
        field="U",
        frame_idx=0,
    )

    assert payload == {
        "odb_id": "odb-1",
        "instance": "PART-1-1",
        "step": "Step-1",
        "field": "U",
        "frame": 0,
        "position": "NODAL",
        "components": ["U1", "U2"],
        "node_labels": [1, 2],
        "values": [[1.0, 2.0], [3.0, 4.0]],
    }


def test_raw_values_route_returns_json_by_default(monkeypatch):
    pytest.importorskip("fastapi")
    from src.l3.api.routes.raw_results import get_raw_result_values

    def fake_get_raw_values(**kwargs):
        return (
            [
                ("node_labels", np.array([1], dtype=np.int32)),
                ("values", np.array([[1.5, 2.5, 3.5]], dtype=np.float32)),
            ],
            ["U1", "U2", "U3"],
            [],
        )

    monkeypatch.setattr("src.l3.api.routes.raw_results.get_raw_values", fake_get_raw_values)

    response = asyncio.run(
        get_raw_result_values(
            odb_id="odb-1",
            instance="PART-1-1",
            step="Step-1",
            field="U",
            frame=0,
            position="NODAL",
        )
    )

    body = json.loads(response.body.decode("utf-8"))
    assert response.headers["x-payload-type"] == "raw_values_json_v1"
    assert body["position"] == "NODAL"
    assert body["node_labels"] == [1]
    assert body["values"] == [[1.5, 2.5, 3.5]]
