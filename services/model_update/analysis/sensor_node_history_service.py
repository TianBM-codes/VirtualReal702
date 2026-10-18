"""Read stored sensor matches and extract directional histories using Abaqus."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlparse

from db import get_connection
from src.l3.core.config import settings
from src.l3.core.errors import AppError, NotFoundError, ValidationError
from src.l3.infra.registry_repo import RegistryRepo
from src.utils.file_fetch import is_http_url, resolve_runner_source
from .model_update_meta_service import resolve_abaqus_command


def _resolve_source(project_id, result_group):
    repo = RegistryRepo(settings.registry_db_path)
    project = repo.get_project(str(project_id))
    if project is None:
        raise NotFoundError("工程不存在", {"project_id": project_id})
    groups = [dict(row) for row in repo.list_result_groups(str(project_id))]
    if result_group is None:
        defaults = [row for row in groups if row["result_group"] == "default_result"]
        candidates = defaults or groups
        if len(candidates) != 1:
            raise ValidationError("请指定 result_group：工程没有唯一的默认结果组",
                                  {"result_groups": [row["result_group"] for row in groups]})
        group = candidates[0]
    else:
        group = next((row for row in groups if row["result_group"] == result_group), None)
        if group is None:
            raise NotFoundError("结果组不存在", {"result_group": result_group})
    if group["status"] != "ready":
        raise ValidationError("结果组尚未就绪", {"result_group": group["result_group"]})
    source = str(group.get("source_path") or "")
    source_name = urlparse(source).path if is_http_url(source) else source
    if Path(source_name).suffix.lower() != ".odb":
        raise ValidationError("测点方向时程目前仅支持 Abaqus ODB 结果组")
    if not is_http_url(source) and not os.path.isfile(source):
        raise NotFoundError("结果组的原始 ODB 文件不存在")
    workspace = repo.resolve_workspace(str(project["workspace"]), settings.data_root)
    source = resolve_runner_source(source, workspace, group.get("source_file"))
    return os.path.abspath(source), group["result_group"]


def _load_matches(project_id, sensors):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        matches = []
        for sensor in sensors:
            cursor.execute("""
                SELECT m.instance_name, m.fem_node_label, m.distance,
                       p.x_position, p.y_position, p.z_position
                FROM t_mt_py_fem_node_match m
                JOIN t_mt_measuring_point_info p
                  ON p.project_id = m.pid AND p.measuring_point_name = m.test_node_id
                WHERE m.pid = %s AND m.test_node_id = %s
                LIMIT 2
            """, (project_id, sensor["name"]))
            rows = cursor.fetchall() or []
            if not rows:
                raise NotFoundError("测点或已保存的节点匹配不存在", {"sensor": sensor["name"]})
            if len(rows) != 1:
                raise ValidationError("测点对应多条匹配或坐标记录", {"sensor": sensor["name"]})
            row = rows[0]
            if not row.get("instance_name") or row.get("fem_node_label") is None:
                raise ValidationError("测点匹配缺少实例或节点号", {"sensor": sensor["name"]})
            matches.append(dict(sensor, instance_name=row["instance_name"],
                                node_label=int(row["fem_node_label"]),
                                coordinates=[float(row[key]) for key in ("x_position", "y_position", "z_position")],
                                distance=None if row["distance"] is None else float(row["distance"])))
        return matches
    finally:
        cursor.close()
        conn.close()


def _extract_sensor_node_history(project_id, sensors, fields, result_group=None):
    source, group = _resolve_source(project_id, result_group)
    matches = _load_matches(project_id, sensors)
    script = Path(__file__).resolve().parents[3] / "tools" / "odb_sensor_history.py"
    with tempfile.TemporaryDirectory(prefix="sensor_history_") as temp_dir:
        request_path = Path(temp_dir) / "request.json"
        output_path = Path(temp_dir) / "history.json"
        request_path.write_text(json.dumps({"odb": source, "sensors": matches, "fields": fields},
                                           ensure_ascii=True), encoding="utf-8")
        command = [resolve_abaqus_command(), "python", str(script), str(request_path), str(output_path)]
        try:
            # Keep potentially large Abaqus logs on disk instead of in server memory.
            with (Path(temp_dir) / "abaqus.log").open("wb") as log:
                process = subprocess.run(command, cwd=temp_dir, stdout=log, stderr=subprocess.STDOUT,
                                         timeout=3600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise AppError("测点结果提取超时", code="EXTRACTION_TIMEOUT", status_code=504) from exc
        except OSError as exc:
            raise AppError("无法启动 Abaqus，请检查 APP_ABAQUS_CMD 配置", code="ABAQUS_UNAVAILABLE") from exc
        payload = None
        if process.returncode == 0 and output_path.is_file():
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError):
                pass
        if not isinstance(payload, dict) or not isinstance(payload.get("sensors"), list):
            log_path = Path(temp_dir) / "abaqus.log"
            with log_path.open("rb") as log:
                log.seek(max(0, log_path.stat().st_size - 4000))
                diagnostic = log.read().decode("utf-8", errors="replace")
            raise AppError("Abaqus 测点结果提取失败", code="EXTRACTION_FAILED",
                           details={"diagnostic": diagnostic})
    return dict(payload, project_id=project_id, result_group=group, direction_coordinate_system="global")


def _history_curves(payload, field):
    curves, matches, issues = [], [], []
    for sensor in payload["sensors"]:
        matches.append({key: sensor[key] for key in ("name", "coordinates", "direction", "match")})
        for step in sensor["steps"]:
            # A separate series for each step avoids joining unrelated local
            # time axes; section points likewise remain separate.
            sections = list(dict.fromkeys(value["section_point"]
                for frame in step["frames"] for item in frame["fields"] for value in item["values"])) or [""]
            for section in sections:
                points = []
                for frame in step["frames"]:
                    item = frame["fields"][0]
                    value = next((row for row in item["values"] if row["section_point"] == section), None)
                    y = value["projection"] if value is not None and value["status"] == "ok" else None
                    points.append([str(frame["frame_value"]), y])
                label = "|".join([sensor["name"], step["step"], field] + ([section] if section else []))
                curves.append({"data": points, "label": label, "xaxis": [point[0] for point in points]})
            for frame in step["frames"]:
                item = frame["fields"][0]
                if item["status"] != "ok":
                    issues.append({"sensor": sensor["name"], "step": step["step"],
                                   "frame_idx": frame["frame_idx"], "status": item["status"],
                                   "message": item.get("message")})
    return {"project_id": payload["project_id"], "result_group": payload["result_group"],
            "fields": field, "curve_type": "sensor_node_history", "data": curves,
            "actual_fields": sorted({item["field"] for sensor in payload["sensors"]
                for step in sensor["steps"] for frame in step["frames"] for item in frame["fields"]}),
            "matches": matches, "issues": issues,
            "direction_coordinate_system": "global",
            "summary": {"sensor_count": len(matches), "curve_count": len(curves)}}


def get_sensor_node_history(project_id, sensors, fields="U", result_group=None):
    field = fields.strip().upper() if isinstance(fields, str) else ""
    if field not in {"U", "S", "E", "LE"}:
        raise ValidationError("fields 必须是 U、S、E、LE 中的一个变量")
    payload = _extract_sensor_node_history(project_id, sensors, [field], result_group)
    return _history_curves(payload, field)
