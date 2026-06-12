"""
Cross-instance batch rename/recolor for scheme=elset, backend-only.

The element-set legend keys are 'INSTANCE.setname'. The LegendEditor lists every
instance's selected sets in one panel (GET legend-entries returns all instances for
elset) and saves them all in one POST. Because the front-end is embedded in a
third-party project and cannot be changed, the POST route must itself route each
override to the instance parsed from its legend_key — without relying on all=true
from the caller.

This test drives the real FastAPI route (no front-end) and asserts each override
lands under its OWNING instance, so it shows up when that instance is rendered.
"""
import os
from types import SimpleNamespace

import h5py
import pytest

from src.inp import parse_inp
from src.inp.exporter import export_l1
from src.l3.infra.manifest_repo import ManifestRepo
from src.l3.services import color_service as C


# Two instances of one part with instance-scoped assembly sets (same model as the
# prefix test, kept self-contained here).
_INP = (
    "*Part, name=PA\n"
    "*Node\n"
    + "".join(f"{i}, {i}.0, 0.0, 0.0\n" for i in range(1, 17))
    + "*Element, type=C3D8R, elset=AllE\n"
    "1, 1, 2, 3, 4, 5, 6, 7, 8\n"
    "2, 9, 10, 11, 12, 13, 14, 15, 16\n"
    "*Solid Section, elset=AllE, material=Steel\n1.,\n"
    "*End Part\n"
    "*Assembly, name=Assembly\n"
    "*Instance, name=PA-1, part=PA\n*End Instance\n"
    "*Instance, name=PA-2, part=PA\n*End Instance\n"
    "*Elset, elset=UserA, instance=PA-1\n1,\n"
    "*Elset, elset=UserB, instance=PA-2\n2,\n"
    "*End Assembly\n"
    "*Material, name=Steel\n*Elastic\n210000., 0.3\n"
)


def _make_app(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.l3.api.routes import color_code

    inp = os.path.join(str(tmp_path), "m.inp")
    with open(inp, "w") as fh:
        fh.write(_INP)
    ws = os.path.join(str(tmp_path), "ws")
    os.makedirs(ws, exist_ok=True)
    export_l1(parse_inp(inp), ws)

    with h5py.File(os.path.join(ws, "l1", "sets", "sets.h5")) as f:
        insts = sorted(f["element_sets"].keys())
    # Full-enough idx: source_elem_etype / render_source_elem_row let the legend
    # builder run; the POST route additionally only needs workspace + instance keys.
    fake_idx = SimpleNamespace(
        workspace=ws,
        odb_id="t",
        is_render_ready=True,
        legend_scan_cache={},
        averaging_data={},
        source_elem_etype={},
        render_source_elem_row={},
    )
    for inst in insts:
        et, row = C._full_element_arrays(fake_idx, inst)
        fake_idx.source_elem_etype[inst] = et
        fake_idx.render_source_elem_row[inst] = row
    monkeypatch.setattr(color_code.registry, "get", lambda odb_id: fake_idx)

    app = FastAPI()
    app.include_router(color_code.router)
    return TestClient(app), ws


def test_elset_save_routes_each_override_to_owning_instance(monkeypatch, tmp_path):
    client, ws = _make_app(monkeypatch, tmp_path)

    # The editor saves the whole panel at once (entries span both instances), and
    # the front-end POSTs to a single URL placeholder instance (PA-1) WITHOUT all=true.
    body = [
        {"legend_key": "PA-1.UserA", "display_name": "Bracket-A",
         "color_r": 1.0, "color_g": 0.0, "color_b": 0.0},
        {"legend_key": "PA-2.UserB", "display_name": "Bracket-B",
         "color_r": 0.0, "color_g": 1.0, "color_b": 0.0},
        {"legend_key": "other", "display_name": None,
         "color_r": None, "color_g": None, "color_b": None},
    ]
    resp = client.post(
        "/api/odb/t/color-code/PA-1/legend-entries",
        params={"scheme": "elset"},
        json=body,
    )
    assert resp.status_code == 200, resp.text

    repo = ManifestRepo(ws)
    ov1 = repo.get_legend_overrides("PA-1", "elset")
    ov2 = repo.get_legend_overrides("PA-2", "elset")

    # PA-1's override lives under PA-1; PA-2's under PA-2 — not both under the URL instance.
    assert ov1.get("PA-1.UserA", {}).get("display_name") == "Bracket-A"
    assert ov1["PA-1.UserA"]["color_r"] == 1.0
    assert "PA-2.UserB" not in ov1

    assert ov2.get("PA-2.UserB", {}).get("display_name") == "Bracket-B"
    assert ov2["PA-2.UserB"]["color_g"] == 1.0
    assert "PA-1.UserA" not in ov2


def test_elset_legend_entries_get_lists_all_instances(monkeypatch, tmp_path):
    client, ws = _make_app(monkeypatch, tmp_path)

    # Editor broadcasts both instances' sets; the GET (no all=true) must still return
    # entries for every instance so the panel can rename/recolor across instances.
    # Set names are uppercased on export (UserA → USERA), so the schemes list — and
    # therefore the broadcast set_names — are uppercase.
    resp = client.get(
        "/api/odb/t/color-code/PA-1/legend-entries",
        params={"scheme": "elset", "set_names": "PA-1.USERA,PA-2.USERB"},
    )
    assert resp.status_code == 200, resp.text
    keys = {e["legend_key"] for e in resp.json()["data"]["entries"]}
    assert "PA-1.USERA" in keys
    assert "PA-2.USERB" in keys
