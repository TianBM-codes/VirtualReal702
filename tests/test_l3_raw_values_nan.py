"""raw-values format=json：NaN/Inf 输出为 null（JSONResponse 不允许 NaN，原来 500）。"""
import json

import numpy as np

from src.l3.services.raw_result_service import raw_values_to_json_payload


def _payload(position, sections, etypes=()):
    return raw_values_to_json_payload(
        sections, ["V"], list(etypes), position=position, odb_id="o",
        instance="PART-1-1", step="s", field="S_MAX_INPLANE_PRINCIPAL", frame_idx=0)


def test_element_nan_becomes_null():
    v = np.array([[[1.0], [np.nan]], [[np.inf], [2.0]]], dtype=np.float32)
    p = _payload("INTEGRATION_POINT",
                 [("el_S4R", np.array([1, 2], np.int32)), ("v_S4R", v)], ["S4R"])
    json.dumps(p, allow_nan=False)
    assert p["etype_groups"][0]["values"] == [[[1.0], [None]], [[None], [2.0]]]


def test_nodal_finite_unchanged():
    v = np.array([[0.5, 1.5]], dtype=np.float32)
    p = _payload("NODAL", [("node_labels", np.array([7], np.int32)), ("values", v)])
    assert p["values"] == v.tolist()
