import json
import os
import posixpath
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests

from db import get_connection
from src.l3.core.errors import NotFoundError, ValidationError

from .model_update_meta_service import _load_service_config


DEFAULT_PBS_APPLICATIONS = {
    "Abaqus": {
        "application_id": "Abaqus",
        "application_name": "Abaqus",
        "version": "2022",
        "cores": 48,
        "hosts": 1,
        "precision": "off",
        "platform": "",
        "primary_file_exts": [".inp"],
        "result_exts": [".odb", ".dat", ".msg", ".sta", ".log", ".prt"],
    },
    "Nastran": {
        "application_id": "Nastran",
        "application_name": "Nastran",
        "version": "2019",
        "cores": 8,
        "memory": 2048,
        "platform": "",
        "primary_file_exts": [".bdf", ".dat", ".nas"],
        "result_exts": [".f06", ".op2", ".pch", ".xdb", ".log", ".out"],
    },
}

API_PATHS = {
    "login": "{auth_path}",
    "expand_vars": "{service_prefix}/pas/restservice/files/expandvars",
    "create_dir": "{service_prefix}/pas/restservice/files/dir/create",
    "upload_file": "{service_prefix}/pas/restservice/files/upload",
    "file_exists": "{service_prefix}/pas/restservice/files/file/exists",
    "submit_job": "{service_prefix}/pas/restservice/jpbs",
    "job_status": "{storage_prefix}/jobs/{job_id}",
    "list_files": "{service_prefix}/pas/restservice/files/file/list",
    "download_file": "{service_prefix}/pas/restservice/files/download",
}

_SERVICE_PREFIX_KEYS = {"expand_vars", "create_dir", "upload_file", "file_exists", "submit_job", "list_files", "download_file"}
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


@dataclass
class PBSEnvironmentConfig:
    name: str
    base_url: str
    api_prefix: str
    service_prefix: str
    storage_prefix: str
    auth_path: str
    server_name: str
    stage_path_template: str
    username: str
    password: str
    verify_ssl: bool
    fallback_service_prefixes: List[str]
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
                "project_config is not valid json",
                {"project_id": int(project_id), "error": str(exc)},
            )
    if not isinstance(project_config, dict):
        return {}
    pbs_config = project_config.get("pbs")
    return dict(pbs_config) if isinstance(pbs_config, dict) else {}


def _normalize_project_application_settings(application: str, pbs_settings: Dict[str, Any]) -> Dict[str, Any]:
    app_name = str(application or "").strip()
    nested = pbs_settings.get(app_name.lower())
    if not isinstance(nested, dict):
        nested = pbs_settings.get(app_name)
    raw = dict(nested) if isinstance(nested, dict) else dict(pbs_settings)

    mapping = {
        "ApplicationId": "application_id",
        "ApplicationName": "application_name",
        "VERSION": "version",
        "CORES": "cores",
        "HOSTS": "hosts",
        "MEMORY": "memory",
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
    fallback_prefixes = list(
        pbs_root.get("fallback_service_prefixes")
        or payload.get("PBS_FALLBACK_SERVICE_PREFIXES")
        or []
    )
    applications = dict(DEFAULT_PBS_APPLICATIONS)
    configured_apps = pbs_root.get("applications") if isinstance(pbs_root.get("applications"), dict) else {}
    configured_apps.update(payload.get("PBS_APPLICATIONS") if isinstance(payload.get("PBS_APPLICATIONS"), dict) else {})
    for app_name, base in configured_apps.items():
        merged = dict(applications.get(app_name, {}))
        merged.update(base or {})
        applications[app_name] = merged

    base_url = str(raw_env.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ValidationError("pbs base_url is required", {"env": selected_env})

    return PBSEnvironmentConfig(
        name=selected_env,
        base_url=base_url,
        api_prefix=_normalize_slash_prefix(raw_env.get("api_prefix")),
        service_prefix=_normalize_slash_prefix(raw_env.get("service_prefix")),
        storage_prefix=_normalize_slash_prefix(raw_env.get("storage_prefix")),
        auth_path=_normalize_slash_prefix(raw_env.get("auth_path")),
        server_name=str(raw_env.get("server_name") or "").strip(),
        stage_path_template=str(raw_env.get("stage_path_template") or "").strip(),
        username=str(raw_env.get("username") or "").strip(),
        password=str(raw_env.get("password") or "").strip(),
        verify_ssl=bool(raw_env.get("verify_ssl", True)),
        fallback_service_prefixes=[_normalize_slash_prefix(item) for item in fallback_prefixes if str(item or "").strip()],
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
        api_prefix=config.api_prefix,
        service_prefix=config.service_prefix,
        storage_prefix=config.storage_prefix,
        auth_path=config.auth_path,
        server_name=config.server_name,
        stage_path_template=config.stage_path_template,
        username=config.username,
        password=config.password,
        verify_ssl=config.verify_ssl,
        fallback_service_prefixes=list(config.fallback_service_prefixes),
        applications=applications,
    )


class PBSClient:
    def __init__(self, config: PBSEnvironmentConfig, *, timeout: int = 60):
        self.config = config
        self.timeout = max(int(timeout or 0), 1)
        self.session = requests.Session()
        self.access_token: Optional[str] = None

    def _candidate_service_prefixes(self) -> List[str]:
        prefixes = [self.config.service_prefix]
        for item in self.config.fallback_service_prefixes:
            if item and item not in prefixes:
                prefixes.append(item)
        return prefixes

    def _build_path_candidates(self, path_key: str, **kwargs) -> List[str]:
        template = API_PATHS[path_key]
        if path_key not in _SERVICE_PREFIX_KEYS:
            return [template.format(
                auth_path=self.config.auth_path,
                service_prefix=self.config.service_prefix,
                storage_prefix=self.config.storage_prefix,
                **kwargs,
            )]

        result = []
        for service_prefix in self._candidate_service_prefixes():
            result.append(template.format(
                auth_path=self.config.auth_path,
                service_prefix=service_prefix,
                storage_prefix=self.config.storage_prefix,
                **kwargs,
            ))
        return result

    def _build_url(self, path: str) -> str:
        combined = f"{self.config.api_prefix}{path}"
        return f"{self.config.base_url}{combined}"

    def _auth_headers(self) -> Dict[str, str]:
        headers = {}
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
        **kwargs,
    ) -> requests.Response:
        timeout_value = max(int(timeout or self.timeout), 1)
        expected = {int(item) for item in expected_statuses}
        last_response = None
        for path in self._build_path_candidates(path_key, **kwargs):
            url = self._build_url(path)
            response = self.session.request(
                method=method.upper(),
                url=url,
                json=json_body,
                data=data,
                files=files,
                params=params,
                headers=self._auth_headers(),
                timeout=timeout_value,
                verify=self.config.verify_ssl,
                stream=stream,
            )
            last_response = response
            if response.status_code in expected:
                return response
            if response.status_code == 404:
                continue
            break

        if last_response is None:
            raise ValidationError("pbs request failed before sending", {"path_key": path_key})
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
            raise ValidationError("pbs username/password is required", {"env": self.config.name})
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
            raise ValidationError("pbs login did not return access_token", {"payload": payload})
        self.access_token = str(token)
        return self.access_token

    def expand_stage_root(self, stage_path_template: Optional[str] = None) -> str:
        template = str(stage_path_template or self.config.stage_path_template or "").strip()
        if not template:
            raise ValidationError("pbs stage_path_template is required", {"env": self.config.name})
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
                value = payload.get(key)
                if isinstance(value, list) and value:
                    return str(value[0])
            for key in ("path", "expanded_path", "stage_root"):
                value = payload.get(key)
                if value:
                    return str(value)
        if isinstance(payload, str) and payload:
            return payload
        raise ValidationError("pbs expandvars returned empty stage_root", {"payload": payload})

    def make_remote_job_dir(self, *, stage_root: str, application: str, job_name: str) -> str:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        suffix = f"{application}_{job_name}_{timestamp}"
        return _safe_posix_join(stage_root, suffix)

    def create_remote_dir(self, remote_job_dir: str) -> Any:
        response = self.request(
            "POST",
            "create_dir",
            expected_statuses=(200,),
            json_body={"path": remote_job_dir},
        )
        return self._parse_json_or_text(response)

    def upload_file(self, *, local_path: str, remote_dir: str) -> str:
        source_path = Path(local_path).expanduser().resolve()
        if not source_path.exists() or not source_path.is_file():
            raise NotFoundError("pbs local input file not found", {"local_path": str(source_path)})
        with source_path.open("rb") as handle:
            response = self.request(
                "POST",
                "upload_file",
                expected_statuses=(200,),
                data={"serversidefilepath": remote_dir},
                files={"attfile": (source_path.name, handle)},
            )
        _ = self._parse_json_or_text(response)
        return _safe_posix_join(remote_dir, source_path.name)

    def file_exists(self, remote_path: str) -> bool:
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
        if not config.get("platform"):
            raise ValidationError(
                "pbs application platform is required",
                {"application": app_name, "env": self.config.name},
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
                "MEMORY": config.get("memory", 2048),
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
            job_id = data.get("jobId") or data.get("id")
            if job_id:
                return str(job_id)
        if isinstance(data, str) and data.strip():
            return data.strip()
        raise ValidationError("pbs submit_job did not return jobId", {"payload": data})

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
        raise ValidationError("pbs job status payload is not json object", {"payload": payload})

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
            json_body={"includeHidden": False, "path": remote_job_dir},
        )
        payload = self._parse_json_or_text(response)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("data", "files", "result", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        raise ValidationError("pbs list_files payload is invalid", {"payload": payload})

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
        if not self.file_exists(remote_primary_file):
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
    input_file: str,
    env: Optional[str] = None,
    job_name: Optional[str] = None,
    output_dir: Optional[str] = None,
    wait: bool = True,
    download_results: bool = True,
    poll_interval_sec: float = 10.0,
    wait_timeout_sec: int = 3600,
    timeout_sec: int = 60,
    submit_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source_path = Path(input_file).expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        raise NotFoundError("pbs local input file not found", {"input_file": str(source_path)})

    project_pbs_settings = _load_project_pbs_settings(project_id)
    resolved_env = _resolve_project_pbs_env(env, project_pbs_settings)
    config = load_pbs_environment_config(resolved_env)
    resolved_application = str(application or "").strip()
    config = _apply_project_pbs_settings(
        config,
        application=resolved_application,
        pbs_settings=project_pbs_settings,
    )
    client = PBSClient(config, timeout=timeout_sec)
    resolved_job_name = str(job_name or source_path.stem).strip() or source_path.stem
    target_dir = Path(output_dir).expanduser().resolve() if output_dir else source_path.parent.resolve()
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
    return result


def get_pbs_job_status(*, job_id: str, env: Optional[str] = None, timeout_sec: int = 60) -> Dict[str, Any]:
    config = load_pbs_environment_config(env)
    client = PBSClient(config, timeout=timeout_sec)
    client.login()
    payload = client.get_job_status(job_id)
    return {
        "env": config.name,
        "job_id": str(job_id),
        "job_status": payload,
        "resolved_job_state": client.extract_job_state(payload),
    }
