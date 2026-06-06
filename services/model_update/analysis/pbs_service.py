import json
import uuid
import os
import posixpath
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests

from db import get_connection
from src.l3.core.config import settings
from src.l3.core.errors import ConflictError, NotFoundError, ValidationError
from src.l3.infra.registry_repo import RegistryRepo

from config import _load_service_config
from .project_file_service import resolve_project_output_dir
from .project_log_service import log_project_error, log_project_info, log_project_step
from .solver_service import (
    _submit_generic_project_result_group_and_wait,
)

DEFAULT_PBS_APPLICATIONS = {
    "Abaqus": {
        "cores": 48,
        "hosts": 1,
        "precision": "off",
        "primary_file_exts": [".inp"],
        "result_exts": [".odb", ".dat", ".msg", ".sta", ".log", ".prt"],
    },
    "Nastran": {
        "cores": 8,
        "primary_file_exts": [".bdf", ".dat", ".nas"],
        "result_exts": [".f06", ".op2", ".pch", ".xdb", ".log", ".out"],
    },
}

DEFAULT_PBS_API_PATHS = {
    "login": "/api/ams/aaservice/authn/oauth2/token",
    "job_query": "/api/storage/jobs/query",
    "dynamic_app_def": "/api/Service6/pas/restservice/applications/dynamicappdef",
    "expand_vars": "/api/Service6/pas/restservice/files/expandvars",
    "create_dir": "/api/Service6/pas/restservice/files/dir/create",
    "upload_file": "/api/Service6/pas/restservice/files/upload",
    "file_exists": "/api/Sservice6/pas/restservice/files/file/exists",
    "submit_job": "/api/Service6/pas/restservice/jobs",
    "job_status": "/api/storage/jobs/{job_id}",
    "list_files": "/api/Sservice6/pas/restservice/files/list",
    "download_file": "/api/Service6/pas/restservice/files/download",
}
_PBS_SUCCESS_VALUES = {True, "true", "True", "TRUE", 1, "1", "success", "SUCCESS"}
_PBS_FINAL_STATES = {"C", "F"}


def _normalize_slash_prefix(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.startswith("/") else f"/{text}"


def _safe_posix_join(*parts: str) -> str:
    cleaned = []
    for part in parts:
        token = str(part or "").strip()
        if not token:
            continue
        cleaned.append(token.strip("/"))
    if not cleaned:
        return ""
    root = "/" if str(parts[0] or "").startswith("/") else ""
    return root + "/".join(cleaned)


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _pick_downloaded_result_file(paths: List[str], suffixes: tuple[str, ...]) -> Optional[str]:
    wanted = {item.lower() for item in suffixes}
    for path in paths:
        suffix = Path(str(path)).suffix.lower()
        if suffix in wanted and Path(str(path)).exists():
            return os.path.abspath(str(path))
    return None


def _project_workspace(project_id: int) -> Path:
    repo = RegistryRepo(settings.registry_db_path)
    row = repo.get_project(str(int(project_id)))
    if row is not None:
        stored_workspace = str(row["workspace"] or "").strip()
        if stored_workspace:
            return Path(
                repo.resolve_workspace(stored_workspace, settings.data_root)
            ).expanduser().resolve()
    return (Path(settings.data_root).expanduser().resolve() / str(int(project_id))).resolve()


def _resolve_project_input_file(project_id: int, path: str, field_name: str) -> Path:
    raw = str(path or "").strip()
    if not raw:
        raise ValidationError(
            f"{field_name} cannot be empty",
            {"project_id": int(project_id), field_name: path},
        )

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        workspace = _project_workspace(project_id)
        resolved = (workspace / candidate).resolve()
        try:
            common = os.path.commonpath([str(workspace), str(resolved)])
        except ValueError as exc:
            raise ValidationError(
                f"{field_name} must stay inside project workspace",
                {"project_id": int(project_id), field_name: raw, "workspace": str(workspace)},
            ) from exc
        if common != str(workspace):
            raise ValidationError(
                f"{field_name} must stay inside project workspace",
                {"project_id": int(project_id), field_name: raw, "workspace": str(workspace)},
            )

    if not resolved.exists() or not resolved.is_file():
        raise NotFoundError(field_name, {field_name: str(resolved), "project_id": int(project_id)})
    return resolved


def _normalize_result_group_name(value: str) -> str:
    text = str(value or "").strip()
    text = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in text)
    text = text.strip("._-")
    if not text:
        raise ValidationError("result_group name cannot be empty", {"value": value})
    return text[:96]


def _submit_local_project_result_group_and_wait(
        *,
        project_id: int,
        source_path: str,
        job_name: str,
        result_group: Optional[str],
        display_name: Optional[str],
        parse_options: Optional[dict],
        default_result_group: Optional[str] = None,
) -> dict:
    resolved_source_path = os.path.abspath(str(source_path))
    if not os.path.exists(resolved_source_path):
        raise NotFoundError("result file not found", {"source_path": resolved_source_path})

    project_key = str(int(project_id))
    repo = RegistryRepo(settings.registry_db_path)
    project_row = repo.get_project(project_key)
    if project_row is None:
        raise NotFoundError("project", {"project_id": int(project_id)})

    geom_status = str(project_row["geom_status"] or "").strip().lower()
    if geom_status != "ready":
        raise ValidationError(
            "project geometry must be ready before importing a local result group",
            {
                "project_id": int(project_id),
                "geom_status": geom_status,
            },
        )

    resolved_result_group = _normalize_result_group_name(
        result_group or default_result_group or f"solver_result_{job_name}_{int(time.time())}"
    )
    resolved_display_name = str(
        display_name or Path(resolved_source_path).stem or resolved_result_group
    ).strip() or resolved_result_group
    resolved_workspace = str(_project_workspace(int(project_id)))

    parse_options_payload = dict(parse_options or {})
    parse_options_payload["display_name"] = resolved_display_name
    parse_options_json = json.dumps(parse_options_payload) if parse_options_payload else None
    source_file = os.path.basename(resolved_source_path)

    existing = repo.get_result_group(project_key, resolved_result_group)
    if existing is not None:
        if str(existing["status"] or "").strip().lower() == "running":
            raise ConflictError(
                f"result_group '{resolved_result_group}' is currently being processed",
                {
                    "project_id": int(project_id),
                    "result_group": resolved_result_group,
                },
            )
        repo.reset_result_group_for_resubmit(
            project_key,
            resolved_result_group,
            resolved_source_path,
            source_file,
            resolved_display_name,
            parse_options_json,
        )
    else:
        repo.create_result_group(
            project_id=project_key,
            result_group=resolved_result_group,
            display_name=resolved_display_name,
            source_path=resolved_source_path,
            source_file=source_file,
            parse_options=parse_options_json,
        )

    from src import job_runner as local_job_runner

    local_job_runner._cleanup_result_group(resolved_workspace, resolved_result_group)
    local_job_runner._update_result_group_status(project_key, resolved_result_group, "running")
    ok = local_job_runner._run_result_group(
        project_key,
        resolved_result_group,
        resolved_source_path,
        parse_options_json,
        resolved_workspace,
    )
    group_row = repo.get_result_group(project_key, resolved_result_group)
    group_payload = dict(group_row) if group_row is not None else None
    status = str((group_payload or {}).get("status") or ("ready" if ok else "error"))
    if not ok or status != "ready":
        raise ValidationError(
            "local project result-group parsing failed",
            {
                "project_id": int(project_id),
                "result_group": resolved_result_group,
                "status": status,
                "source_path": resolved_source_path,
                "project_result_group": group_payload,
            },
        )

    return {
        "result_group": resolved_result_group,
        "display_name": resolved_display_name,
        "status": status,
        "project_id": int(project_id),
        "source_path": resolved_source_path,
        "parse_options": parse_options_payload,
        "workspace": resolved_workspace,
        "project_result_group": group_payload,
    }


@dataclass
class PBSEnvironmentConfig:
    name: str
    base_url: str
    server_name: str
    stage_path_template: str
    username: str
    password: str
    verify_ssl: bool
    api_paths: Dict[str, str]
    applications: Dict[str, Dict[str, Any]]


def _load_project_pbs_settings(project_id: Optional[int]) -> Dict[str, Any]:
    if project_id is None:
        return {}
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT project_config
            FROM t_mt_work_condition_project
            WHERE project_id = %s
            LIMIT 1
            """,
            (int(project_id),),
        )
        row = cursor.fetchone() or {}
    finally:
        cursor.close()
        conn.close()

    raw_config = row.get("project_config")
    if not raw_config:
        return {}
    if isinstance(raw_config, dict):
        project_config = raw_config
    else:
        try:
            project_config = json.loads(raw_config)
        except Exception as exc:
            raise ValidationError(
                "project_config 不是合法的 JSON",
                {"project_id": int(project_id), "error": str(exc)},
            )
    if not isinstance(project_config, dict):
        return {}
    pbs_config = project_config.get("pbs")
    return dict(pbs_config) if isinstance(pbs_config, dict) else {}


def _ensure_project_pbs_settings(project_id: Optional[int], pbs_settings: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(pbs_settings, dict) and pbs_settings:
        return pbs_settings
    raise ValidationError(
        "请先配置高性能集群计算配置",
        {
            "project_id": int(project_id) if project_id is not None else None,
            "field": "project_config.pbs",
        },
    )


def _normalize_project_application_settings(application: str, pbs_settings: Dict[str, Any]) -> Dict[str, Any]:
    app_name = str(application or "").strip()
    nested = pbs_settings.get(app_name.lower())
    if not isinstance(nested, dict):
        nested = pbs_settings.get(app_name)
    if not isinstance(nested, dict):
        apps = pbs_settings.get("applications")
        if isinstance(apps, dict):
            nested = apps.get(app_name) or apps.get(app_name.lower())
    raw = dict(nested) if isinstance(nested, dict) else dict(pbs_settings)

    mapping = {
        "ApplicationId": "application_id",
        "ApplicationName": "application_name",
        "VERSION": "version",
        "CORES": "cores",
        "HOSTS": "hosts",
        "PRECISION": "precision",
        "PLATFORM": "platform",
        "primary_file_exts": "primary_file_exts",
        "result_exts": "result_exts",
    }
    normalized: Dict[str, Any] = {}
    for source_key, target_key in mapping.items():
        if source_key in raw and raw[source_key] is not None:
            normalized[target_key] = raw[source_key]
    return normalized


def _resolve_project_pbs_env(env: Optional[str], pbs_settings: Dict[str, Any]) -> Optional[str]:
    explicit = str(env or "").strip()
    if explicit:
        return explicit
    configured = pbs_settings.get("env")
    if configured is None:
        return None
    resolved = str(configured).strip()
    return resolved or None


def load_pbs_environment_config(env: Optional[str] = None) -> PBSEnvironmentConfig:
    payload = _load_service_config()
    pbs_root = payload.get("PBS") if isinstance(payload.get("PBS"), dict) else {}
    environments = pbs_root.get("environments") if isinstance(pbs_root.get("environments"), dict) else {}
    selected_env = str(env or pbs_root.get("env") or payload.get("PBS_ENV") or "").strip() or "dev"

    if not environments and isinstance(payload.get("PBS_ENVIRONMENTS"), dict):
        environments = payload["PBS_ENVIRONMENTS"]
    if selected_env not in environments:
        raise ValidationError(
            "pbs environment config not found",
            {
                "env": selected_env,
                "configured_envs": sorted(environments.keys()),
            },
        )

    raw_env = dict(environments[selected_env] or {})
    applications = {app_name: dict(base or {}) for app_name, base in DEFAULT_PBS_APPLICATIONS.items()}

    base_url = str(raw_env.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ValidationError("PBS base_url 不能为空", {"env": selected_env})

    api_paths = dict(DEFAULT_PBS_API_PATHS)
    root_api_paths = pbs_root.get("api_paths") if isinstance(pbs_root.get("api_paths"), dict) else {}
    env_api_paths = raw_env.get("api_paths") if isinstance(raw_env.get("api_paths"), dict) else {}
    for source in (root_api_paths, env_api_paths):
        for key, value in source.items():
            text = _normalize_slash_prefix(value)
            if text:
                api_paths[str(key)] = text

    if not all(api_paths.get(key) for key in (
            "login", "expand_vars", "create_dir", "upload_file", "file_exists", "submit_job", "job_status",
            "list_files", "download_file")):
        raise ValidationError(
            "PBS 接口路径配置不完整",
            {"env": selected_env, "api_paths": api_paths},
        )

    return PBSEnvironmentConfig(
        name=selected_env,
        base_url=base_url,
        server_name=str(raw_env.get("server_name") or "").strip(),
        stage_path_template=str(raw_env.get("stage_path_template") or "").strip(),
        username=str(raw_env.get("username") or "").strip(),
        password=str(raw_env.get("password") or "").strip(),
        verify_ssl=bool(raw_env.get("verify_ssl", True)),
        api_paths=api_paths,
        applications=applications,
    )


def _apply_project_pbs_settings(
        config: PBSEnvironmentConfig,
        *,
        application: str,
        pbs_settings: Dict[str, Any],
) -> PBSEnvironmentConfig:
    override_app = _normalize_project_application_settings(application, pbs_settings)
    if not override_app:
        return config
    applications = dict(config.applications)
    merged_app = dict(applications.get(application, {}))
    merged_app.update(override_app)
    applications[application] = merged_app
    return PBSEnvironmentConfig(
        name=config.name,
        base_url=config.base_url,
        server_name=config.server_name,
        stage_path_template=config.stage_path_template,
        username=config.username,
        password=config.password,
        verify_ssl=config.verify_ssl,
        api_paths=dict(config.api_paths),
        applications=applications,
    )


class PBSClient:
    def __init__(self, config: PBSEnvironmentConfig, *, timeout: int = 60):
        self.config = config
        self.timeout = max(int(timeout or 0), 1)
        self.session = requests.Session()
        self.access_token: Optional[str] = None

    def _build_path(self, path_key: str, **kwargs) -> str:
        template = self.config.api_paths.get(path_key)
        if not template:
            raise ValidationError(
                "PBS 接口路径未配置",
                {"path_key": path_key, "env": self.config.name},
            )
        return str(template).format(**kwargs)

    def _build_url(self, path: str) -> str:
        return f"{self.config.base_url}{path}"

    def _auth_headers(self) -> Dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.access_token:
            headers["access_token"] = self.access_token
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def request(
            self,
            method: str,
            path_key: str,
            *,
            expected_statuses: Iterable[int] = (200,),
            json_body: Any = None,
            data: Any = None,
            files: Any = None,
            params: Optional[Dict[str, Any]] = None,
            stream: bool = False,
            timeout: Optional[int] = None,
            headers=None,
            **kwargs,
    ) -> requests.Response:
        timeout_value = max(int(timeout or self.timeout), 1)
        expected = {int(item) for item in expected_statuses}
        path = self._build_path(path_key, **kwargs)
        url = self._build_url(path)
        if json_body is None:
            json_body = {}
        if data is None:
            data = {}
        body = {**json_body, **data}
        print("\n" + "=" * 80)
        print(f"[PBS REQUEST] {method.upper()} {url}")
        print(f"Path Key: {path_key}")
        print(f"Timeout: {timeout_value}s")
        print(f"Stream: {stream}")
        print("-" * 80)
        print("Headers:")
        print(json.dumps(headers or {}, ensure_ascii=False, indent=2, default=str))
        print("-" * 80)
        print("Query Params:")
        print(json.dumps(params or {}, ensure_ascii=False, indent=2, default=str))
        print("-" * 80)
        print("JSON Body:")
        print(json.dumps(json_body, ensure_ascii=False, indent=2, default=str) if json_body is not None else "null")
        print("-" * 80)
        print("Form Data:")
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str) if data is not None else "null")
        print("-" * 80)
        print("Files:")
        if isinstance(files, dict) and files:
            file_debug = {}
            for key, value in files.items():
                if isinstance(value, (list, tuple)) and value:
                    file_debug[key] = {
                        "filename": value[0],
                        "content_type": value[2] if len(value) > 2 else None,
                    }
                else:
                    file_debug[key] = str(value)
            print(json.dumps(file_debug, ensure_ascii=False, indent=2, default=str))
        else:
            print("{}")
        print("=" * 80)
        last_response = self.session.request(
            method=method.upper(),
            url=url,
            json=json.dumps(body) if len(body) != 0 else None,
            data=json.dumps(body) if len(body) != 0 else None,
            files=files,
            params=params,
            headers=self._auth_headers() if headers is None else headers,
            timeout=timeout_value,
            verify=self.config.verify_ssl,
            stream=stream,
        )
        if last_response.status_code in expected:
            return last_response
        raise ValidationError(
            "pbs request failed",
            {
                "path_key": path_key,
                "status_code": int(last_response.status_code),
                "response_text": last_response.text[-2000:],
            },
        )

    def _parse_json_or_text(self, response: requests.Response) -> Any:
        content_type = str(response.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            return response.json()
        text = response.text.strip()
        if not text:
            return None
        try:
            return response.json()
        except ValueError:
            return text

    def login(self) -> str:
        if not self.config.username or not self.config.password:
            raise ValidationError("PBS 用户名或密码不能为空", {"env": self.config.name})
        response = self.request(
            "POST",
            "login",
            expected_statuses=(200,),
            data={
                "grant_type": "password",
                "username": self.config.username,
                "password": self.config.password,
            },
        )
        payload = self._parse_json_or_text(response)
        token = None
        if isinstance(payload, dict):
            token = payload.get("access_token") or payload.get("token")
        elif isinstance(payload, str):
            token = payload.strip()
        if not token:
            raise ValidationError("PBS 登录未返回 access_token", {"payload": payload})
        self.access_token = str(token)
        return self.access_token

    def expand_stage_root(self, stage_path_template: Optional[str] = None) -> str:
        template = str(stage_path_template or self.config.stage_path_template or "").strip()
        if not template:
            raise ValidationError("PBS stage_path_template 不能为空", {"env": self.config.name})
        response = self.request(
            "POST",
            "expand_vars",
            expected_statuses=(200,),
            json_body={"paths": [template]},
        )
        payload = self._parse_json_or_text(response)
        if isinstance(payload, list) and payload:
            return str(payload[0])
        if isinstance(payload, dict):
            for key in ("paths", "data", "result", "results"):
                value = payload.get("data").get(key)
                if isinstance(value, list) and value:
                    return str(value[0])
            for key in ("path", "expandedPaths", "stage_root"):
                value = payload.get("data").get(key)
                if value:
                    return str(value[template])
        if isinstance(payload, str) and payload:
            return payload
        raise ValidationError("PBS expandvars 返回的 stage_root 为空", {"payload": payload})

    def make_remote_job_dir(self, *, stage_root: str, application: str, job_name: str) -> str:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        suffix = f"{application}_{job_name}_{timestamp}"
        return _safe_posix_join(stage_root, suffix)

    def create_remote_dir(self, remote_job_dir: str) -> Any:
        response = self.request(
            "POST",
            "create_dir",
            expected_statuses=(200, 201),
            json_body={"path": remote_job_dir},
        )
        return self._parse_json_or_text(response)

    def upload_file(self, *, local_path: str, remote_dir: str) -> str:
        source_path = Path(local_path).expanduser().resolve()
        if not source_path.exists() or not source_path.is_file():
            raise NotFoundError("未找到 PBS 本地输入文件", {"local_path": str(source_path)})
        if not self.access_token:
            raise ValidationError("PBS 尚未登录，缺少 access_token", {"env": self.config.name})
        with source_path.open("rb") as handle:
            response = self.request(
                "POST",
                "upload_file",
                expected_statuses=(200,),
                params={
                    "serversidefilepath": remote_dir + "/" + source_path.name,
                    "access_token": self.access_token,
                    "uid": uuid.uuid4(),
                },
                headers={"access_token": f"{self.access_token}"},
                files={"attfile": (source_path.name, handle)},
            )
        result = self._parse_json_or_text(response)
        if not result['success']:
            raise ValidationError("pbs upload file failed", result)
        else:
            print("Upload Success _t")
        return _safe_posix_join(remote_dir, source_path.name)

    def file_exists(self, remote_path: List[str]) -> bool:
        response = self.request(
            "POST",
            "file_exists",
            expected_statuses=(200,),
            json_body={"paths": remote_path},
        )
        payload = self._parse_json_or_text(response)
        if isinstance(payload, dict):
            for key in ("fileExists", "exists", "success"):
                value = payload.get(key)
                if value in _PBS_SUCCESS_VALUES:
                    return True
                if value is False or value == "false":
                    return False
            data = payload.get("data")
            if isinstance(data, dict):
                return self._is_trueish(data.get("fileExists") or data.get("exists"))
        return self._is_trueish(payload)

    @staticmethod
    def _is_trueish(value: Any) -> bool:
        return value in _PBS_SUCCESS_VALUES

    def get_application_config(self, application: str) -> Dict[str, Any]:
        app_name = str(application or "").strip()
        if app_name not in self.config.applications:
            raise ValidationError(
                "pbs application config not found",
                {"application": app_name, "applications": sorted(self.config.applications.keys())},
            )
        config = _json_copy(self.config.applications[app_name])
        missing_fields = [
            field
            for field in ("application_id", "application_name", "version", "platform")
            if not str(config.get(field) or "").strip()
        ]
        if missing_fields:
            raise ValidationError(
                "PBS 应用配置不完整，请先在工程 project_config.pbs 中配置",
                {
                    "application": app_name,
                    "env": self.config.name,
                    "missing_fields": missing_fields,
                    "field": "project_config.pbs",
                },
            )
        return config

    def build_submit_payload(
            self,
            *,
            application: str,
            job_name: str,
            remote_primary_file: str,
            remote_job_dir: str,
            overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        config = self.get_application_config(application)
        payload = {
            "ApplicationId": config["application_id"],
            "ApplicationName": config["application_name"],
            "JOB_NAME": job_name,
            "PLATFORM": config["platform"],
            "PRIMARY_FILE": [{"value": remote_primary_file}],
            "SUBMISSION_DIRECTORY": {"value": remote_job_dir},
            "VERSION": config["version"],
            "myapp": "OtherApps",
        }
        if application == "Abaqus":
            payload.update({
                "CORES": config.get("cores", 48),
                "HOSTS": config.get("hosts", 1),
                "PRECISION": config.get("precision", "off"),
            })
        elif application == "Nastran":
            payload.update({
                "CORES": config.get("cores", 8),
            })
        if overrides:
            payload.update({str(key): value for key, value in overrides.items()})
        return payload

    def submit_job(self, *, application: str, payload: Dict[str, Any]) -> str:
        app_config = self.get_application_config(application)
        response = self.request(
            "POST",
            "submit_job",
            expected_statuses=(200,),
            params={
                "application_id": app_config["application_id"],
                "server_registered_name": self.config.server_name,
            },
            json_body=payload,
        )
        data = self._parse_json_or_text(response)
        if isinstance(data, dict):
            job_id = data.get("data").get("jobId") or data.get("id")
            if job_id:
                return str(job_id)
        if isinstance(data, str) and data.strip():
            return data.strip()
        raise ValidationError("PBS submit_job 未返回 jobId", {"payload": data})

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        response = self.request(
            "GET",
            "job_status",
            expected_statuses=(200,),
            params={"serverName": self.config.server_name},
            job_id=job_id,
        )
        payload = self._parse_json_or_text(response)
        if isinstance(payload, dict):
            return payload
        raise ValidationError("PBS 任务状态返回结果不是 JSON 对象", {"payload": payload})

    def wait_until_done(
            self,
            job_id: str,
            *,
            poll_interval_sec: float = 10.0,
            wait_timeout_sec: int = 3600,
    ) -> Dict[str, Any]:
        poll_interval = max(float(poll_interval_sec or 0), 0.1)
        timeout_sec = max(int(wait_timeout_sec or 0), 1)
        started_at = time.monotonic()
        last_payload = None
        while True:
            payload = self.get_job_status(job_id)
            last_payload = payload
            state = self.extract_job_state(payload)
            if state in _PBS_FINAL_STATES:
                payload["resolved_job_state"] = state
                return payload

            if time.monotonic() - started_at >= timeout_sec:
                raise ValidationError(
                    "pbs wait_until_done timed out",
                    {
                        "job_id": job_id,
                        "wait_timeout_sec": timeout_sec,
                        "last_payload": last_payload,
                    },
                )
            time.sleep(poll_interval)

    @staticmethod
    def extract_job_state(payload: Dict[str, Any]) -> Optional[str]:
        for key in ("jobState", "state", "job_state", "status"):
            value = payload.get(key)
            if value is not None:
                return str(value)
        data = payload.get("data")
        if isinstance(data, dict):
            return PBSClient.extract_job_state(data)
        return None

    def list_result_files(self, remote_job_dir: str) -> List[Dict[str, Any]]:
        response = self.request(
            "POST",
            "list_files",
            expected_statuses=(200,),
            params={"page": 1, "size": 100, "jobstatus": "undefined", "sortby": "ctime", "sortorder": "DSC"},
            json_body={"includeHidden": False, "path": remote_job_dir},
        )
        payload = self._parse_json_or_text(response)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("data", "files", "result", "results"):
                value = payload.get('data').get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        raise ValidationError("PBS list_files 返回结果无效", {"payload": payload})

    def download_file(self, *, remote_file: str, local_dir: str, job_id: str) -> str:
        local_root = Path(local_dir).expanduser().resolve()
        local_root.mkdir(parents=True, exist_ok=True)
        file_name = posixpath.basename(remote_file)
        target = local_root / file_name
        cookie_value = f"downloadCookie_{int(time.time())}"

        response = self.request(
            "GET",
            "download_file",
            expected_statuses=(200,),
            params={
                "serversidefilepath": remote_file,
                "jobid": job_id,
                "downladCookie": cookie_value,
                "dodelete": "false",
            },
            stream=True,
        )
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    handle.write(chunk)
        return str(target)

    def run_job(
            self,
            *,
            application: str,
            local_primary_file: str,
            job_name: str,
            output_dir: str,
            wait: bool = True,
            download_results: bool = True,
            poll_interval_sec: float = 10.0,
            wait_timeout_sec: int = 3600,
            submit_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.login()
        stage_root = self.expand_stage_root()
        remote_job_dir = self.make_remote_job_dir(
            stage_root=stage_root,
            application=application,
            job_name=job_name,
        )
        self.create_remote_dir(remote_job_dir)
        remote_primary_file = self.upload_file(local_path=local_primary_file, remote_dir=remote_job_dir)
        if not self.file_exists([remote_primary_file]):
            raise ValidationError(
                "pbs primary file upload failed",
                {
                    "local_primary_file": str(local_primary_file),
                    "remote_primary_file": remote_primary_file,
                },
            )
        submit_payload = self.build_submit_payload(
            application=application,
            job_name=job_name,
            remote_primary_file=remote_primary_file,
            remote_job_dir=remote_job_dir,
            overrides=submit_overrides,
        )
        job_id = self.submit_job(application=application, payload=submit_payload)
        result = {
            "env": self.config.name,
            "application": application,
            "job_name": job_name,
            "job_id": job_id,
            "remote_job_dir": remote_job_dir,
            "remote_primary_file": remote_primary_file,
            "stage_root": stage_root,
            "submit_payload": submit_payload,
            "status": "submitted",
        }
        if not wait:
            return result

        status_payload = self.wait_until_done(
            job_id,
            poll_interval_sec=poll_interval_sec,
            wait_timeout_sec=wait_timeout_sec,
        )
        resolved_state = self.extract_job_state(status_payload)
        result["job_status"] = status_payload
        result["resolved_job_state"] = resolved_state

        files = self.list_result_files(remote_job_dir)
        result["result_files"] = files
        result_exts = {str(item).lower() for item in self.get_application_config(application).get("result_exts", [])}
        filtered = []
        for item in files:
            remote_path = str(item.get("absPath") or item.get("path") or item.get("fullPath") or item.get("name") or "")
            if not remote_path:
                continue
            suffix = posixpath.splitext(remote_path)[1].lower()
            if suffix in result_exts:
                filtered.append(remote_path)
        result["result_file_paths"] = filtered

        downloaded = []
        if download_results:
            for remote_path in filtered:
                downloaded.append(
                    self.download_file(
                        remote_file=remote_path,
                        local_dir=output_dir,
                        job_id=job_id,
                    )
                )
        result["downloaded_files"] = downloaded
        if resolved_state == "F":
            raise ValidationError(
                "pbs job finished with failed state",
                result,
            )
        return result


def run_pbs_solver_job(
        *,
        project_id: Optional[int] = None,
        application: str,
        input_file: Optional[str] = None,
        input_file_name: Optional[str] = None,
        env: Optional[str] = None,
        job_name: Optional[str] = None,
        output_dir: Optional[str] = None,
        output_dir_name: Optional[str] = None,
        result_group: Optional[str] = None,
        display_name: Optional[str] = None,
        base_url: Optional[str] = None,
        step: Optional[str] = None,
        frame: Optional[int] = None,
        field_prefix: Optional[str] = None,
        upload_timeout: int = 60,
        wait: bool = True,
        download_results: bool = True,
        poll_interval_sec: float = 10.0,
        wait_timeout_sec: int = 3600,
        timeout_sec: int = 60,
        submit_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        if project_id is not None:
            log_project_step(
                int(project_id),
                f"PBS {application} run started",
                stage="pbs_run_started",
                percent=0,
            )

        resolved_input = str(input_file or "").strip() or str(input_file_name or "").strip()
        resolved_field_name = "input_file" if str(input_file or "").strip() else "input_file_name"
        if not resolved_input:
            raise ValidationError(
                "input_file or input_file_name is required",
                {
                    "project_id": int(project_id) if project_id is not None else None,
                    "input_file": input_file,
                    "input_file_name": input_file_name,
                },
            )

        if project_id is not None:
            source_path = _resolve_project_input_file(int(project_id), resolved_input, resolved_field_name)
        else:
            source_path = Path(resolved_input).expanduser().resolve()
            if not source_path.exists() or not source_path.is_file():
                raise NotFoundError(resolved_field_name, {resolved_field_name: str(source_path)})
        if project_id is not None:
            log_project_info(
                int(project_id),
                f"Resolved PBS input file: {source_path}",
                stage="path_resolved",
                percent=5,
            )

        project_pbs_settings = _ensure_project_pbs_settings(project_id, _load_project_pbs_settings(project_id))
        resolved_env = _resolve_project_pbs_env(env, project_pbs_settings)
        config = load_pbs_environment_config(resolved_env)
        resolved_application = str(application or "").strip()
        config = _apply_project_pbs_settings(
            config,
            application=resolved_application,
            pbs_settings=project_pbs_settings,
        )
        if project_id is not None:
            log_project_info(
                int(project_id),
                f"PBS config validated: env={config.name}, application={resolved_application}",
                stage="pbs_config_ready",
                percent=10,
            )

        client = PBSClient(config, timeout=timeout_sec)
        resolved_job_name = str(job_name or source_path.stem).strip() or source_path.stem
        resolved_output_dir = str(output_dir or "").strip() or str(output_dir_name or "").strip()
        if project_id is not None:
            target_dir = resolve_project_output_dir(
                int(project_id),
                category_parts=(),
                explicit_dir=resolved_output_dir or None,
            )
        else:
            target_dir = Path(resolved_output_dir).expanduser().resolve() if resolved_output_dir else source_path.parent.resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        primary_ext = source_path.suffix.lower()
        app_config = client.get_application_config(resolved_application)
        allowed_exts = {str(item).lower() for item in app_config.get("primary_file_exts", [])}
        if allowed_exts and primary_ext not in allowed_exts:
            raise ValidationError(
                "pbs primary file extension is not supported for application",
                {
                    "application": resolved_application,
                    "input_file": str(source_path),
                    "input_ext": primary_ext,
                    "allowed_exts": sorted(allowed_exts),
                },
            )

        if project_id is not None:
            log_project_step(
                int(project_id),
                f"Submitting PBS job: {resolved_job_name}",
                stage="pbs_submit",
                percent=20,
            )
            if wait:
                log_project_info(
                    int(project_id),
                    "PBS job will wait for completion and download outputs if requested",
                    stage="pbs_wait",
                    percent=30,
                )

        result = client.run_job(
            application=resolved_application,
            local_primary_file=str(source_path),
            job_name=resolved_job_name,
            output_dir=str(target_dir),
            wait=wait,
            download_results=download_results,
            poll_interval_sec=poll_interval_sec,
            wait_timeout_sec=wait_timeout_sec,
            submit_overrides=submit_overrides,
        )
        result["input_file"] = str(source_path)
        result["output_dir"] = str(target_dir)
        result["project_id"] = int(project_id) if project_id is not None else None
        result["workflow"] = f"pbs_{resolved_application.lower()}_run"
        if wait and project_id is not None:
            downloaded_files = list(result.get("downloaded_files") or [])
            if resolved_application == "Abaqus":
                odb_path = _pick_downloaded_result_file(downloaded_files, (".odb",))
                if odb_path:
                    parse_options = {
                        "consistency_check": "count-only",
                        "invariants": "none",
                    }
                    if step:
                        parse_options["steps"] = [str(step)]
                    if frame is not None:
                        parse_options["frames"] = [int(frame)]
                    else:
                        parse_options["frames"] = "all"
                    upload = _submit_local_project_result_group_and_wait(
                        project_id=int(project_id),
                        source_path=odb_path,
                        job_name=resolved_job_name,
                        result_group=result_group,
                        display_name=display_name,
                        parse_options=parse_options,
                        default_result_group="default_result",
                    )
                    result["upload"] = upload
                    result["uploaded_result_file"] = odb_path
            elif resolved_application == "Nastran":
                op2_path = _pick_downloaded_result_file(downloaded_files, (".op2",))
                if op2_path:
                    upload = _submit_generic_project_result_group_and_wait(
                        project_id=int(project_id),
                        source_path=op2_path,
                        job_name=resolved_job_name,
                        result_group=result_group,
                        display_name=display_name,
                        base_url=base_url,
                        parse_options=None,
                        timeout=upload_timeout,
                        wait_timeout_sec=wait_timeout_sec,
                        poll_interval_sec=poll_interval_sec,
                    )
                    result["upload"] = upload
                    result["uploaded_result_file"] = op2_path

        if project_id is not None:
            job_id = result.get("job_id") or result.get("id") or ""
            if wait:
                downloaded_count = len(result.get("downloaded_files") or [])
                log_project_step(
                    int(project_id),
                    f"PBS job finished: job_id={job_id}, downloaded_files={downloaded_count}",
                    stage="pbs_finished",
                    percent=100,
                )
            else:
                log_project_step(
                    int(project_id),
                    f"PBS job submitted: job_id={job_id}",
                    stage="pbs_submitted",
                    percent=40,
                )
        return result
    except Exception as exc:
        if project_id is not None:
            log_project_error(
                int(project_id),
                f"PBS {application} run failed: {exc}",
                stage="failed",
            )
        raise


def get_pbs_job_status(*, project_id: int, job_id: str, env: Optional[str] = None, timeout_sec: int = 60) -> Dict[str, Any]:
    project_pbs_settings = _ensure_project_pbs_settings(project_id, _load_project_pbs_settings(project_id))
    resolved_env = _resolve_project_pbs_env(env, project_pbs_settings)
    config = load_pbs_environment_config(resolved_env)
    client = PBSClient(config, timeout=timeout_sec)
    client.login()
    payload = client.get_job_status(job_id)
    return {
        "env": config.name,
        "project_id": int(project_id),
        "job_id": str(job_id),
        "job_status": payload,
        "resolved_job_state": client.extract_job_state(payload),
    }
