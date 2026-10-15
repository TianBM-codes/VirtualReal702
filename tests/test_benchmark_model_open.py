"""First-open timings must cover all surfaces and reject invalid speedups."""
from contextlib import nullcontext
import hashlib

import pytest

from tools import benchmark_model_open as B


def _setup(monkeypatch, *, faces=4, changed=False):
    calls = []
    monkeypatch.setattr(B.requests, "Session", lambda: nullcontext(object()))

    def overview(*args, **kwargs):
        calls.append("metadata")
        return {"json": {"instances": [{"instance_name": "PART/1"},
                                        {"instance_name": "SOLID 2"}]},
                "elapsed_ms": 10.0, "bytes": 200}

    def download(session, url, timeout):
        calls.append(url)
        return {"elapsed_ms": 20.0, "bytes": 1000,
                "sha256": str(len(calls)) if changed else "stable",
                "face_count": str(faces) if "chunked" in url else None,
                "edge_count": "2" if "feature-edges" in url else None,
                "chunk_count": "1" if "chunked" in url else None}

    monkeypatch.setattr(B.bench, "_request", overview)
    monkeypatch.setattr(B, "download", download)
    # Discovery would preload the model and invalidate the first-open timing.
    monkeypatch.setattr(B.bench, "_discover", lambda *a: pytest.fail("preloaded before first open"))
    return calls


def test_first_open_covers_all_instances_from_metadata(monkeypatch):
    calls = _setup(monkeypatch)
    rows = B.measure_open({"name": "new", "project_id": "bench", "base_url": "http://localhost"},
                          {"repeat": 2})
    assert calls[0] == "metadata"
    assert len(calls) == 10
    assert "PART%2F1/feature-edges" in calls[1]
    assert "SOLID%202/feature-edges" in calls[2]
    assert "PART%2F1/render-buffers-chunked" in calls[3]
    assert "SOLID%202/render-buffers-chunked" in calls[4]
    totals = [r for r in rows if r["case"] == "open_surface_http_total"]
    assert [r["temperature"] for r in totals] == ["first", "repeat"]


@pytest.mark.parametrize("faces,changed,message", [
    (0, False, "No surface faces"),
    (4, True, "Geometry changed"),
])
def test_invalid_or_changed_geometry_cannot_be_reported_as_speedup(monkeypatch, faces, changed, message):
    _setup(monkeypatch, faces=faces, changed=changed)
    with pytest.raises(RuntimeError, match=message):
        B.measure_open({"name": "new", "project_id": "bench", "base_url": "http://localhost"},
                       {"repeat": 2})


def test_odb_upgrade_only_receives_an_independent_verified_copy(tmp_path):
    original = tmp_path / "model.odb"
    original.write_bytes(b"original odb bytes")
    before = original.stat()
    staged, info, _elapsed = B.stage_odb(original, tmp_path / "results")
    assert staged != original and staged.name == original.name
    assert staged.is_relative_to(tmp_path / "results")
    assert info["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    staged.write_bytes(b"upgraded bytes")  # What the legacy service may do.
    assert original.read_bytes() == b"original odb bytes"
    assert original.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize("corrupt_original", [False, True])
def test_changed_or_corrupt_source_copy_is_rejected(tmp_path, monkeypatch, corrupt_original):
    original = tmp_path / "model.odb"
    original.write_bytes(b"original odb bytes")
    copy = B.shutil.copy2

    def faulty_copy(source, destination):
        copy(source, destination)
        (source if corrupt_original else destination).write_bytes(b"corrupt")

    monkeypatch.setattr(B.shutil, "copy2", faulty_copy)
    with pytest.raises(RuntimeError, match="Staged ODB does not match"):
        B.stage_odb(original, tmp_path / "results")
