"""报告不应将空边线响应当成性能收益；区域请求必须用内部标签。"""
from tools import benchmark_versions as B


def _row(version, run, edges):
    return {"version": version, "project_id": "bench", "case": "region_mesh_edges_material",
            "run": run, "temperature": "first" if run == 1 else "repeat",
            "status": 200, "elapsed_ms": 100.0, "server_ms": 80.0,
            "bytes": 120 + edges * 24, "cache": "", "edge_count": edges, "region": "Steel"}


def test_empty_old_region_suppresses_performance_ratios(tmp_path):
    versions = [{"name": "old", "project_id": "old", "base_url": "http://localhost"},
                {"name": "new", "project_id": "new", "base_url": "http://localhost"}]
    rows = [_row(version, run, edges) for version, edges in (("old", 0), ("new", 4))
            for run in (1, 2, 3)]
    B._write_outputs(rows, versions, tmp_path)
    report = next(tmp_path.glob("*_report_*.md")).read_text(encoding="utf-8")
    assert "| region_mesh_edges_material | 100.0 ms | 100.0 ms | - | - |" in report
    assert "| region_mesh_edges_material | 80.0 ms | 80.0 ms | 80.0 ms | 80.0 ms | - | - |" in report
    assert "| region_mesh_edges_material | 0 | 4 | DIFFERENT count/region" in report
    assert "edge_count,region" in next(tmp_path.glob("*_raw_*.csv")).read_text(encoding="utf-8-sig")


def test_region_label_change_is_not_comparable():
    versions = [{"name": "old"}, {"name": "new"}]
    old, new = _row("old", 1, 4), _row("new", 1, 4)
    new["region"] = "Aluminium"
    assert not B._summary([old, new], versions)[0]["comparable"]


def test_detail_requests_use_visible_internal_key(monkeypatch):
    ctx = {"project_id": "bench", "instance": "PART/1", "step": "Step",
           "result_group": "rg", "color_code_schemes": ["material"]}
    monkeypatch.setattr(B, "_discover", lambda *args: ctx)
    monkeypatch.setattr(B, "_cases", lambda *args: [])
    requests = []

    def request(url, timeout, **kwargs):
        requests.append((url, kwargs.get("collect_json")))
        result = {"status": 200, "elapsed_ms": 10.0, "server_ms": 5.0,
                  "bytes": 512, "cache": "", "edge_count": ""}
        if kwargs.get("collect_json"):
            result["json"] = {"entries": [
                {"legend_key": "Empty", "face_count": 0},
                {"legend_key": "Steel", "display_name": "用户改名", "face_count": 20},
                {"legend_key": "Aluminium", "face_count": 2},
            ]}
        return result

    monkeypatch.setattr(B, "_request", request)
    version = {"name": "new", "base_url": "http://localhost"}
    rows = B._benchmark_version(version, {"repeat": 2})
    assert len(rows) == 10  # 图例、两种统计、两种区域接口，每项两次。
    assert version["color_code_regions"] == {"material": "Steel"}
    region_urls = [url for url, _ in requests if "/region-" in url]
    assert len(region_urls) == 4
    assert all("PART%2F1/region-" in url and "region=Steel" in url for url in region_urls)
    assert all("json" not in row for row in rows)  # JSON 仅用于选择区域，不塞入原始 CSV。
