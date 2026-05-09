import re
from typing import Callable, Match

from fastapi.responses import JSONResponse

from src.l3.core.errors import AppError


_EXACT_ERROR_TRANSLATIONS = {
    "input_inp not found": "未找到 input_inp 文件",
    "odb file not found": "未找到 odb 文件",
    "workspace path is too broad to rebuild safely": "workspace 路径范围过大，无法安全重建",
    "unexpected result block rank": "结果块维度异常",
    "no usable result blocks found": "未找到可用的结果块",
    "frame_indices contains values outside the available range": "frame_indices 中包含超出可用范围的值",
    "no readable result blocks found": "未找到可读取的结果块",
    "components contains unknown names": "components 中包含未知名称",
    "failed to build workspace from odb": "从 odb 构建 workspace 失败",
    "value_mode must be either 'inherit' or 'explicit'": "value_mode 只能是 'inherit' 或 'explicit'",
    "output_inp must not overwrite input_inp": "output_inp 不能覆盖 input_inp",
    "DSA config is not complete enough to generate inp": "DSA 配置尚不完整，无法生成 inp",
    "failed to generate DSA inp from database config": "根据数据库配置生成 DSA inp 失败",
    "project-driven sensitivity inp generator is not implemented yet": "基于项目配置生成灵敏度 inp 的功能尚未实现",
    "result_group name cannot be empty": "result_group 名称不能为空",
    "project result-group api target project was not found": "项目结果组接口未找到目标项目",
    "project result-group api request failed": "项目结果组接口请求失败",
    "project was not found while polling result-group status": "轮询结果组状态时未找到项目",
    "failed to poll project result-group status": "轮询项目结果组状态失败",
    "project result-group extraction failed": "项目结果组提取失败",
    "timed out waiting for project result-group to become ready": "等待项目结果组就绪超时",
    "parameter_columns are required for sensitivity cloud export": "灵敏度云图导出需要 parameter_columns",
    "response_rows are required for sensitivity cloud export": "灵敏度云图导出需要 response_rows",
    "no element targets were resolved for sensitivity cloud export": "灵敏度云图导出未解析到任何单元目标",
    "odb_id is required for cloud export via external-field api": "通过 external-field 接口导出云图时必须提供 odb_id",
    "external-field api target odb was not found": "external-field 接口未找到目标 odb",
    "step is required because multiple steps are available": "存在多个 step 时必须显式指定 step",
    "design response variable is empty": "设计响应变量为空",
    "DSA field has no mapped optimization parameter": "DSA 字段未映射到任何优化参数",
    "DSA field has no values": "DSA 字段没有值",
    "unsupported source_mode for response field resolution": "不支持当前 source_mode 的响应字段解析",
    "no optimization parameters found for DSA VTU export": "DSA VTU 导出未找到优化参数",
    "m and n must be >= 1": "m 和 n 必须大于等于 1",
    "row index exceeds file length": "行号超出文件长度",
    "column index exceeds row length": "列号超出该行长度",
    "failed to parse numeric values from the selected text row": "无法从所选文本行解析数值",
    "bayesian update requires scalar normalized sensitivities and scalar response values": "Bayesian 修正要求归一化灵敏度和响应值均为标量",
    "bayesian update does not accept non-finite scalar values": "Bayesian 修正不接受非有限标量值",
    "scatter must be > 0": "scatter 必须大于 0",
    "batch_no must be > 0": "batch_no 必须大于 0",
    "r_target and r_model size mismatch": "r_target 和 r_model 大小不匹配",
    "response_values and target_values size mismatch": "response_values 和 target_values 大小不匹配",
    "iteration_results must not be empty for cloud export": "导出云图时 iteration_results 不能为空",
    "parameter_columns are required for cloud export": "导出云图时需要 parameter_columns",
    "bayesian cloud export currently supports element targets only": "Bayesian 云图导出当前仅支持单元目标",
    "no element targets were resolved for bayesian cloud export": "Bayesian 云图导出未解析到任何单元目标",
    "text matrix rows have inconsistent column counts": "文本矩阵各行列数不一致",
    "some parameter values could not be resolved from the inp file": "无法从 inp 文件解析部分参数值",
    "S_norm must be a 2D matrix": "S_norm 必须是二维矩阵",
    "some parameters were not found under any *PARAMETER block": "部分参数未在任何 *PARAMETER 块中找到",
    "no usable design response definition was found in the inp file": "inp 文件中未找到可用的设计响应定义",
    "unable to map DSA field to a model-update parameter": "无法将 DSA 字段映射到模型修正参数",
    "multiple design responses match the requested DSA field": "有多个设计响应匹配所请求的 DSA 字段",
    "unable to resolve a normalized design-response sensitivity map": "无法解析归一化设计响应灵敏度映射",
    "inconsistent parameter metadata found across DSA result blocks": "不同 DSA 结果块中的参数元数据不一致",
    "no overlapping response labels were found for DSA normalization": "DSA 归一化时未找到重叠的响应标签",
    "inconsistent current response values found while building the normalized sensitivity matrix": "构建归一化灵敏度矩阵时发现当前响应值不一致",
    "conflicting normalized sensitivity values found while building the sensitivity matrix": "构建灵敏度矩阵时发现归一化灵敏度值冲突",
    "normalized sensitivity matrix is incomplete for the resolved response rows": "归一化灵敏度矩阵对已解析响应行不完整",
    "abaqus sensitivity rerun failed during bayesian update": "Bayesian 修正过程中重新执行 Abaqus 灵敏度分析失败",
    "nodal displacement field 'U' not found in workspace": "workspace 中未找到节点位移场 'U'",
    "some instances are not available in the selected workspace result": "所选 workspace 结果中缺少部分 instance",
    "iterations must be > 0": "iterations 必须大于 0",
    "exit_diff_percent must be >= 0": "exit_diff_percent 必须大于等于 0",
    "run_solver must be enabled when iterations > 1": "iterations 大于 1 时必须启用 run_solver",
    "workspace, odb_path, or odb_id is required when run_solver is disabled": "禁用 run_solver 时必须提供 workspace、odb_path 或 odb_id",
    "input_inp is required when parameter_values is not provided": "未提供 parameter_values 时必须提供 input_inp",
    "target response vector length does not match model response length": "目标响应向量长度与模型响应长度不一致",
    "cloud export via external-field api requires odb_id or a workspace already loaded in the L3 registry": "通过 external-field 接口导出云图时，必须提供 odb_id 或已加载到 L3 注册表中的 workspace",
    "test model dimensions are not available": "试验模型尺寸不可用",
    "fem model dimensions are not available": "计算模型尺寸不可用",
}

_REGEX_ERROR_TRANSLATIONS = [
    (re.compile(r"^manifest\.db not found under workspace '(.+)'$"), lambda m: f"workspace '{m.group(1)}' 下未找到 manifest.db"),
    (re.compile(r"^unsupported aggregation '(.+)'$"), lambda m: f"不支持的聚合方式 '{m.group(1)}'"),
    (re.compile(r"^position '(.+)' not found for the selected result$"), lambda m: f"所选结果中未找到 position '{m.group(1)}'"),
    (re.compile(r"^(.+) '(.+)' not found$"), lambda m: f"未找到 {m.group(1)} '{m.group(2)}'"),
    (re.compile(r"^(.+) is required because multiple values are available$"), lambda m: f"存在多个可选值时必须显式指定 {m.group(1)}"),
    (re.compile(r"^(.+) is required$"), lambda m: f"{m.group(1)} 不能为空"),
    (re.compile(r"^(.+) must be > 0$"), lambda m: f"{m.group(1)} 必须大于 0"),
    (re.compile(r"^(.+) must be >= 0$"), lambda m: f"{m.group(1)} 必须大于等于 0"),
    (re.compile(r"^(.+) must be >= 1$"), lambda m: f"{m.group(1)} 必须大于等于 1"),
    (re.compile(r"^(.+) must be a file$"), lambda m: f"{m.group(1)} 必须是文件"),
    (re.compile(r"^(.+) must not be empty$"), lambda m: f"{m.group(1)} 不能为空"),
    (re.compile(r"^(.+) is empty$"), lambda m: f"{m.group(1)} 为空"),
    (re.compile(r"^(.+) size mismatch$"), lambda m: f"{m.group(1)} 大小不匹配"),
    (re.compile(r"^inp file not found: (.+)$"), lambda m: f"未找到 inp 文件: {m.group(1)}"),
    (re.compile(r"^odb file not found: (.+)$"), lambda m: f"未找到 odb 文件: {m.group(1)}"),
    (re.compile(r"^result h5 file not found: (.+)$"), lambda m: f"未找到结果 h5 文件: {m.group(1)}"),
    (re.compile(r"^result file not found for step='(.+)' field='(.+)'$"), lambda m: f"未找到 step='{m.group(1)}'、field='{m.group(2)}' 对应的结果文件"),
    (re.compile(r"^result blocks not found for field '(.+)'$"), lambda m: f"未找到字段 '{m.group(1)}' 对应的结果块"),
    (re.compile(r"^result block not found for field '(.+)'$"), lambda m: f"未找到字段 '{m.group(1)}' 对应的结果块"),
    (re.compile(r"^no result blocks for instance '(.+)'$"), lambda m: f"instance '{m.group(1)}' 没有结果块"),
    (re.compile(r"^no frames found for step '(.+)'$"), lambda m: f"step '{m.group(1)}' 下未找到帧数据"),
    (re.compile(r"^field '(.+)' not found$"), lambda m: f"未找到字段 '{m.group(1)}'"),
    (re.compile(r"^frame (\d+) not found under step '(.+)'$"), lambda m: f"step '{m.group(2)}' 下未找到第 {m.group(1)} 帧"),
    (re.compile(r"^ODB '(.+)' not found in loaded registry$"), lambda m: f"已加载注册表中未找到 ODB '{m.group(1)}'"),
    (re.compile(r"^project '(.+)' not found$"), lambda m: f"未找到项目 '{m.group(1)}'"),
    (re.compile(r"^project '(.+)' not found in registry$"), lambda m: f"注册表中未找到项目 '{m.group(1)}'"),
    (re.compile(r"^source inp file not found for project_id=(.+)$"), lambda m: f"未找到 project_id={m.group(1)} 对应的源 inp 文件"),
]


def _translate_error_message(message: str) -> str:
    text = str(message or "")
    if not text:
        return text
    translated = _EXACT_ERROR_TRANSLATIONS.get(text)
    if translated is not None:
        return translated
    for pattern, renderer in _REGEX_ERROR_TRANSLATIONS:
        match = pattern.fullmatch(text)
        if match:
            return renderer(match)
    return text


def server_error(exc: Exception) -> AppError:
    # Normalize unexpected exceptions into the same shape used by the
    # application-specific AppError hierarchy.
    return AppError(message=_translate_error_message(str(exc)), status_code=500)


def success_response(data=None, message: str = "成功") -> dict:
    # Keep a stable success envelope across all routers so the frontend and
    # debug tooling can parse responses without endpoint-specific branches.
    return {
        "ok": True,
        "code": 200,
        "message": str(message),
        "data": data,
    }


def error_response(
    status_code: int,
    message: str,
    *,
    error_code: str = "INTERNAL_ERROR",
    details: dict = None,
):
    # Use JSONResponse so FastAPI preserves the requested HTTP status code
    # together with the shared error payload structure.
    resolved_message = _translate_error_message(message)
    return JSONResponse(
        status_code=int(status_code),
        content={
            "ok": False,
            "code": int(status_code),
            "message": resolved_message,
            "data": None,
            "error": {
                "code": str(error_code),
                "message": resolved_message,
                "details": details or {},
            },
        },
    )
