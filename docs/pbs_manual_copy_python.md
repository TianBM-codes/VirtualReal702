# PBS 手工补丁可抄版

适用范围：

- 从 `4d8ddfa6b4be4982708c42dd9a686ed11119edca`
- 到当前 `HEAD`

如果你只是为了让另一台离线电脑的 PBS 调试功能对齐，优先改这 3 个文件：

1. `services/model_update/analysis/pbs_service.py`
2. `webapi/models.py`
3. `webapi/routers/solver.py`

## 1. 先用 git 看精确改动

在当前仓库执行：

```powershell
git diff 4d8ddfa6b4be4982708c42dd9a686ed11119edca..HEAD -- services/model_update/analysis/pbs_service.py
git diff 4d8ddfa6b4be4982708c42dd9a686ed11119edca..HEAD -- webapi/models.py
git diff 4d8ddfa6b4be4982708c42dd9a686ed11119edca..HEAD -- webapi/routers/solver.py
```

如果你想只看某次提交：

```powershell
git show a5b35fa -- services/model_update/analysis/pbs_service.py webapi/models.py webapi/routers/solver.py
git show 237f1f2 -- services/model_update/analysis/pbs_service.py
git show ce111d8 -- webapi/routers/solver.py
```

## 2. `pbs_service.py` 直接照抄

### 2.1 文件头部导入和常量

把文件头部相关部分改成下面这样：

```python
from .model_update_meta_service import _load_service_config
from .project_log_service import log_project_error, log_project_info, log_project_step


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
    "list_files": "/api/Sservice6/pas/restservice/files/file/list",
    "download_file": "/api/Service6/pas/restservice/files/download",
}
_PBS_SUCCESS_VALUES = {True, "true", "True", "TRUE", 1, "1", "success", "SUCCESS"}
_PBS_FINAL_STATES = {"C", "F"}
```

注意：

- 旧版的 `API_PATHS` 要删掉
- 旧版的 `_SERVICE_PREFIX_KEYS` 要删掉
- 默认应用配置里不要再写 `application_id/application_name/version/platform/memory`

### 2.2 `PBSEnvironmentConfig`

把 dataclass 改成下面这样：

```python
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
```

也就是删掉这些旧字段：

```python
api_prefix
service_prefix
storage_prefix
auth_path
fallback_service_prefixes
```

### 2.3 新增 `_ensure_project_pbs_settings`

在 `_load_project_pbs_settings()` 后面加上：

```python
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
```

### 2.4 替换 `_normalize_project_application_settings`

把这个函数替换成：

```python
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
```

注意：

- `MEMORY` 映射不要了
- 支持 `pbs.applications.Abaqus` 这种嵌套配置

### 2.5 替换 `load_pbs_environment_config`

把整个函数替换成：

```python
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

    if not all(api_paths.get(key) for key in ("login", "expand_vars", "create_dir", "upload_file", "file_exists", "submit_job", "job_status", "list_files", "download_file")):
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
```

### 2.6 替换 `_apply_project_pbs_settings`

```python
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
```

### 2.7 替换 `PBSClient` 里的路径拼装方法

旧版这几段都删掉：

```python
def _candidate_service_prefixes(...)
def _build_path_candidates(...)
```

换成：

```python
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
```

### 2.8 替换 `PBSClient.request`

```python
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
    path = self._build_path(path_key, **kwargs)
    url = self._build_url(path)
    last_response = self.session.request(
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
```

### 2.9 替换 `get_application_config`

```python
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
```

### 2.10 `build_submit_payload` 里删掉 Nastran 的 `MEMORY`

Nastran 分支应改成：

```python
elif application == "Nastran":
    payload.update({
        "CORES": config.get("cores", 8),
    })
```

不要再有：

```python
"MEMORY": config.get("memory", 2048),
```

### 2.11 替换 `run_pbs_solver_job`

把整个函数替换成：

```python
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
    try:
        if project_id is not None:
            log_project_step(
                int(project_id),
                f"PBS {application} run started",
                stage="pbs_run_started",
                percent=0,
            )

        source_path = Path(input_file).expanduser().resolve()
        if not source_path.exists() or not source_path.is_file():
            raise NotFoundError("PBS local input file not found", {"input_file": str(source_path)})
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
```

### 2.12 替换 `get_pbs_job_status`

```python
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
```

## 3. `webapi/models.py` 直接照抄

### 3.1 新增 `SolverRunAndParseRequest`

插到 `AbaqusInpRunAndUploadResultRequest` 后面即可：

```python
class SolverRunAndParseRequest(BaseModel):
    project_id: int
    input_file: str
    job_name: Optional[str] = None
    result_group: Optional[str] = None
    display_name: Optional[str] = None
    base_url: Optional[str] = None
    output_dir: Optional[str] = None
    output_bdf: Optional[str] = None
    abaqus: Optional[str] = None
    nastran: Optional[str] = None
    cpus: Optional[int] = None
    interactive: bool = True
    timeout_sec: Optional[int] = None
    extra_args: List[str] = Field(default_factory=list)
    settings: Dict[str, Any] = Field(default_factory=dict)
    step: Optional[str] = None
    frame: Optional[int] = None
    field_prefix: Optional[str] = None
    upload_timeout: int = 60
    wait_timeout_sec: int = 3600
    poll_interval_sec: float = 2.0
    async_submit: bool = False
```

### 3.2 修改 `PBSSolverRunRequest`

把：

```python
project_id: Optional[int] = None
```

改成：

```python
project_id: int
```

### 3.3 修改 `PBSJobStatusRequest`

改成：

```python
class PBSJobStatusRequest(BaseModel):
    project_id: int
    env: Optional[str] = None
    job_id: str
    timeout_sec: int = 60
```

## 4. `webapi/routers/solver.py` 直接照抄

### 4.1 追加导入

```python
from starlette.concurrency import run_in_threadpool
```

在 `solver_service` 导入里追加：

```python
run_solver_and_parse_project_result,
```

在 models 导入里追加：

```python
SolverRunAndParseRequest,
```

### 4.2 新增 `_solver_run_and_parse_kwargs`

```python
def _solver_run_and_parse_kwargs(body: SolverRunAndParseRequest) -> dict:
    return {
        "project_id": body.project_id,
        "input_file": body.input_file,
        "job_name": body.job_name,
        "result_group": body.result_group,
        "display_name": body.display_name,
        "base_url": body.base_url,
        "output_dir": body.output_dir,
        "output_bdf": body.output_bdf,
        "abaqus": body.abaqus,
        "nastran": body.nastran,
        "cpus": body.cpus,
        "interactive": body.interactive,
        "timeout_sec": body.timeout_sec,
        "extra_args": body.extra_args,
        "settings": body.settings,
        "step": body.step,
        "frame": body.frame,
        "field_prefix": body.field_prefix,
        "upload_timeout": body.upload_timeout,
        "wait_timeout_sec": body.wait_timeout_sec,
        "poll_interval_sec": body.poll_interval_sec,
    }
```

### 4.3 修改 PBS 状态查询接口

把：

```python
data = get_pbs_job_status(
    env=body.env,
    job_id=body.job_id,
    timeout_sec=body.timeout_sec,
)
```

改成：

```python
data = get_pbs_job_status(
    project_id=body.project_id,
    env=body.env,
    job_id=body.job_id,
    timeout_sec=body.timeout_sec,
)
```

### 4.4 新增 `/solver/run_and_parse` 接口

```python
@router.post("/solver/run_and_parse")
async def run_solver_and_parse_api(request: Request, body: SolverRunAndParseRequest):
    await log_request(request, model_to_dict(body))
    try:
        kwargs = _solver_run_and_parse_kwargs(body)
        if body.async_submit:
            data = submit_background_task(
                task_type="solver.run_and_parse",
                fn=run_solver_and_parse_project_result,
                kwargs=kwargs,
                request_payload=model_to_dict(body),
            )
            return success_response(data, "统一计算并解析任务已提交")
        data = await run_in_threadpool(run_solver_and_parse_project_result, **kwargs)
        return success_response(data, "统一计算并解析成功")
    except AppError as exc:
        return error_response(exc.status_code, exc.message, error_code=exc.code, details=exc.details)
    except Exception as exc:
        app_exc = server_error(exc)
        return error_response(app_exc.status_code, app_exc.message, error_code=app_exc.code, details=app_exc.details)
```

## 5. 最小补丁顺序

如果你在另一台机器上想最快抄完：

1. 先改 `webapi/models.py`
2. 再改 `webapi/routers/solver.py`
3. 最后整块替换 `services/model_update/analysis/pbs_service.py` 里的相关函数

## 6. 我建议你离线机重点核对的 4 个点

1. `PBSSolverRunRequest.project_id` 必须是必填
2. `get_pbs_job_status()` 必须接收 `project_id`
3. `PBSClient` 必须改成 `api_paths` 方案
4. Nastran 提交载荷里不能再带 `MEMORY`
