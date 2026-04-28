# -*- coding: utf-8 -*-
"""
updateModelByJson.py

JSON-driven Bayesian parameter update (multi-response).

特性:
    1. 支持节点位移 / 单元应力 混合响应 (由 extract_odb.py 落盘后读取)
    2. 所有配置必须通过 JSON 显式提供 (脚本内部不再保留默认配置)
    3. Bayesian 归一化更新 + 回溯线搜索 + CCABS 收敛判断

用法:
    python updateModelByJson.py <config_json>
"""

import os
import re
import csv
import json
import time
import subprocess

import numpy as np
import matplotlib.pyplot as plt

try:
    from services.model_update.analysis.ccmetrics import (
        build_ccdis as _shared_build_ccdis,
        build_ccmean as _shared_build_ccmean,
        build_cctot as _shared_build_cctot,
    )
except ImportError:  # pragma: no cover - direct script execution fallback
    from ccmetrics import (  # type: ignore
        build_ccdis as _shared_build_ccdis,
        build_ccmean as _shared_build_ccmean,
        build_cctot as _shared_build_cctot,
    )


# =============================================================================
# Config
# =============================================================================
class Config:
    """
    从 JSON 读取全部配置, 不保留任何硬编码默认值.
    缺失字段直接抛 RuntimeError.
    """

    def __init__(self, json_path):
        if json_path is None:
            raise RuntimeError("Config JSON path is required.")
        if not os.path.isfile(json_path):
            raise RuntimeError("Config JSON not found: %s" % json_path)

        self.CONFIG_JSON = json_path

        with open(json_path, "r", encoding="utf-8-sig") as f:
            d = json.load(f)

        if not isinstance(d, dict):
            raise RuntimeError("Config JSON root must be an object.")

        self._assign(d)
        self._validate()

    # --------- 必需字段读取 ---------
    @staticmethod
    def _req(obj, *path):
        cur = obj
        walked = []
        for k in path:
            walked.append(k)
            if not isinstance(cur, dict) or k not in cur:
                raise RuntimeError("Config JSON missing key: %s" % ".".join(walked))
            cur = cur[k]
        return cur

    def _assign(self, d):
        R = self._req

        # paths
        self.ABAQUS_CMD     = str(R(d, "paths", "abaqus_cmd"))
        self.WORK_DIR       = str(R(d, "paths", "work_dir"))
        self.JOB_NAME       = str(R(d, "paths", "job_name"))
        self.INP_FILE       = str(R(d, "paths", "inp_file"))
        self.ODB_FILE       = str(R(d, "paths", "odb_file"))
        self.PARAMETER_FILE = str(R(d, "paths", "parameter_file"))
        self.EXTRACT_SCRIPT = str(R(d, "paths", "extract_script"))

        # outputs
        self.RESP_CSV               = str(R(d, "outputs", "resp_csv"))
        self.SENS_CSV               = str(R(d, "outputs", "sens_csv"))
        self.HISTORY_CSV            = str(R(d, "outputs", "history_csv"))
        self.LOG_FILE               = str(R(d, "outputs", "log_file"))
        self.PARAM_HISTORY_CSV      = str(R(d, "outputs", "param_history_csv"))
        self.SENS_HISTORY_CSV       = str(R(d, "outputs", "sens_history_csv"))
        self.TRIAL_HISTORY_CSV      = str(R(d, "outputs", "trial_history_csv"))
        self.SUBMIT_LOG             = str(R(d, "outputs", "submit_log"))
        self.EXTRACT_LOG            = str(R(d, "outputs", "extract_log"))
        self.RESPONSE_HISTORY_PNG   = str(R(d, "outputs", "response_history_png"))
        self.RESIDUAL_HISTORY_PNG   = str(R(d, "outputs", "residual_history_png"))
        self.PARAM_NORM_HISTORY_PNG = str(R(d, "outputs", "param_norm_history_png"))

        # parameter_update
        self.N_PARAM        = int(R(d, "parameter_update", "n_param"))
        self.PARAM_NAMES    = [str(x) for x in R(d, "parameter_update", "param_names")]
        self.INITIAL_THICK  = float(R(d, "parameter_update", "initial_thick"))
        self.INITIAL_VALUES = R(d, "parameter_update", "initial_values")  # 可以为 null
        self.BOUNDS_LO      = float(R(d, "parameter_update", "bounds_lo"))
        self.BOUNDS_HI      = float(R(d, "parameter_update", "bounds_hi"))
        self.MAX_DELTA      = float(R(d, "parameter_update", "max_delta"))

        # iteration_control
        self.MAX_ITERS   = int(R(d, "iteration_control", "max_iters"))
        self.MIN_ITERS   = int(R(d, "iteration_control", "min_iters"))
        self.STALL_LIMIT = int(R(d, "iteration_control", "stall_limit"))

        # tolerances
        self.TOL_CCABS          = float(R(d, "tolerances", "tol_ccabs"))
        self.TOL_REL_RES        = float(R(d, "tolerances", "tol_rel_res"))
        self.TOL_MAX_ABS_DPARAM = float(R(d, "tolerances", "tol_max_abs_dparam"))
        self.TOL_DX             = float(R(d, "tolerances", "tol_dx"))
        self.TOL_REL_IMPROVE    = float(R(d, "tolerances", "tol_rel_improve"))

        # step_control
        self.SCALE_INIT    = float(R(d, "step_control", "scale_init"))
        self.SCALE_MIN     = float(R(d, "step_control", "scale_min"))
        self.SCALE_MAX     = float(R(d, "step_control", "scale_max"))
        self.SCALE_GROW    = float(R(d, "step_control", "scale_grow"))
        self.SCALE_SHRINK  = float(R(d, "step_control", "scale_shrink"))
        self.MAX_BACKTRACK = int(R(d, "step_control", "max_backtrack"))

        # bayesian
        self.P_SCATTER = float(R(d, "bayesian", "p_scatter"))
        self.R_SCATTER = float(R(d, "bayesian", "r_scatter"))
        self.DAMPING   = float(R(d, "bayesian", "damping"))
        self.EPS       = float(R(d, "bayesian", "eps"))

        # responses
        if "responses" not in d or not isinstance(d["responses"], list) or not d["responses"]:
            raise RuntimeError("Config JSON missing non-empty 'responses' list.")
        self.RESPONSES = [dict(r) for r in d["responses"]]

    def _validate(self):
        if self.N_PARAM <= 0:
            raise RuntimeError("N_PARAM must be positive.")
        if len(self.PARAM_NAMES) != self.N_PARAM:
            raise RuntimeError(
                "len(PARAM_NAMES) != N_PARAM : %d != %d" %
                (len(self.PARAM_NAMES), self.N_PARAM)
            )
        if self.BOUNDS_LO > self.BOUNDS_HI:
            raise RuntimeError("BOUNDS_LO cannot be greater than BOUNDS_HI.")

        valid_types = {"displacement", "stress"}

        for i, rsp in enumerate(self.RESPONSES):
            if "name" not in rsp:
                raise RuntimeError("RESPONSES[%d] missing 'name'" % i)
            if "component" not in rsp:
                raise RuntimeError("RESPONSES[%d] missing 'component'" % i)
            if "exp_value" not in rsp:
                raise RuntimeError("RESPONSES[%d] missing 'exp_value'" % i)

            rtype = str(rsp.get("type", "displacement")).strip().lower()
            if rtype not in valid_types:
                raise RuntimeError(
                    "RESPONSES[%d] unsupported type: %s (valid: %s)" %
                    (i, rtype, sorted(valid_types))
                )
            rsp["type"] = rtype
            rsp["component"] = str(rsp["component"]).strip().upper()

            if rtype == "displacement":
                if "node_label" not in rsp:
                    raise RuntimeError(
                        "RESPONSES[%d] (displacement) missing 'node_label'" % i
                    )
            elif rtype == "stress":
                if "element_label" not in rsp:
                    raise RuntimeError(
                        "RESPONSES[%d] (stress) missing 'element_label'" % i
                    )


# =============================================================================
# 文件辅助
# =============================================================================
def ensure_parent_dir(path):
    folder = os.path.dirname(os.path.abspath(path))
    if folder and (not os.path.isdir(folder)):
        os.makedirs(folder)


def append_csv_row(path, header, row):
    ensure_parent_dir(path)
    write_header = (not os.path.isfile(path)) or (os.path.getsize(path) == 0)

    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(header)
        w.writerow(row)


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def write_text(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def vec_to_text(x, fmt="%.10e"):
    x = np.asarray(x, dtype=float).reshape(-1)
    return ";".join(fmt % v for v in x)


def fmt_vec_log(x, fmt="%.6e"):
    x = np.asarray(x, dtype=float).reshape(-1)
    return "[" + ", ".join(fmt % v for v in x) + "]"


# =============================================================================
# Response / Scatter 读取
# =============================================================================
def get_response_names(cfg):
    return [str(item["name"]) for item in cfg.RESPONSES]


def get_exp_response_vector(cfg):
    return np.array([float(item["exp_value"]) for item in cfg.RESPONSES], dtype=float)


def get_response_scatter_vector(cfg):
    out = []
    for item in cfg.RESPONSES:
        if "scatter" in item and item["scatter"] is not None:
            out.append(float(item["scatter"]))
        else:
            out.append(float(cfg.R_SCATTER))
    return np.array(out, dtype=float)


def build_initial_parameter_vector(cfg):
    if cfg.INITIAL_VALUES is not None:
        arr = np.array(cfg.INITIAL_VALUES, dtype=float).reshape(-1)
        if arr.size != cfg.N_PARAM:
            raise RuntimeError(
                "INITIAL_VALUES size mismatch: expected %d, got %d" %
                (cfg.N_PARAM, arr.size)
            )
        return arr
    return np.full(cfg.N_PARAM, cfg.INITIAL_THICK, dtype=float)


# =============================================================================
# parameter.inp 回写
# =============================================================================
def update_parameters_in_parameter_inp(parameter_inp_path, param_names, param_values):
    if len(param_names) != len(param_values):
        raise ValueError("param_names and param_values size mismatch")

    lines = read_text(parameter_inp_path)

    in_param_block = False
    name_to_value = {name.upper(): value for name, value in zip(param_names, param_values)}
    found = set()

    for i, line in enumerate(lines):
        s = line.strip()
        su = s.upper()

        if su.startswith("*PARAMETER"):
            in_param_block = True
            continue

        if in_param_block:
            if s.startswith("*"):
                break

            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([Ee0-9+\-.]+)\s*$", s)
            if m:
                raw_name = m.group(1)
                name = raw_name.upper()
                if name in name_to_value:
                    lines[i] = "%s=%.15g\n" % (raw_name, name_to_value[name])
                    found.add(name)

    missing = [n for n in name_to_value if n not in found]
    if missing:
        raise RuntimeError(
            "Parameters not found in parameter.inp *PARAMETER block: %s" %
            missing[:10]
        )

    write_text(parameter_inp_path, lines)


# =============================================================================
# Abaqus 提交 / 后处理调用
# =============================================================================
def clean_job_files(cfg):
    work_dir = os.path.abspath(cfg.WORK_DIR)
    for ext in (
        ".odb", ".dat", ".msg", ".sta", ".com", ".prt", ".sim", ".log",
        ".lck", ".023", ".mdl", ".stt",
    ):
        fn = os.path.join(work_dir, cfg.JOB_NAME + ext)
        if os.path.isfile(fn):
            try:
                os.remove(fn)
            except OSError:
                pass


def run_abaqus_job(cfg):
    job_name = cfg.JOB_NAME
    work_dir = os.path.abspath(cfg.WORK_DIR)
    inp_path = os.path.join(work_dir, cfg.INP_FILE)

    if not os.path.isdir(work_dir):
        raise RuntimeError("WORK_DIR does not exist: %s" % work_dir)
    if not os.path.isfile(inp_path):
        raise RuntimeError("INP file not found: %s" % inp_path)

    clean_job_files(cfg)

    cmd = 'cmd /c ""%s" job=%s input=%s interactive"' % (
        cfg.ABAQUS_CMD, job_name, cfg.INP_FILE,
    )

    print("=" * 72)
    print("[JOB] Working directory : %s" % work_dir)
    print("[JOB] INP path          : %s" % inp_path)
    print("[JOB] Command           : %s" % cmd)
    print("=" * 72)

    t0 = time.time()
    result = subprocess.run(
        cmd,
        cwd=work_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=True,
    )
    dt = time.time() - t0

    print("[JOB] Return code = %d | %.1f s" % (result.returncode, dt))
    print("[JOB] Output:")
    print(result.stdout)

    submit_log = os.path.join(work_dir, cfg.SUBMIT_LOG)
    ensure_parent_dir(submit_log)
    with open(submit_log, "w", encoding="utf-8", errors="ignore") as f:
        f.write(result.stdout)

    if result.returncode != 0:
        raise RuntimeError(
            "Abaqus submission failed (rc=%d). See %s" %
            (result.returncode, submit_log)
        )

    sta_file = os.path.join(work_dir, job_name + ".sta")
    odb_file = os.path.join(work_dir, job_name + ".odb")
    msg_file = os.path.join(work_dir, job_name + ".msg")
    dat_file = os.path.join(work_dir, job_name + ".dat")
    log_file = os.path.join(work_dir, job_name + ".log")

    appeared = False
    for _ in range(20):
        if any(os.path.isfile(p) for p in (sta_file, odb_file, msg_file, dat_file, log_file)):
            appeared = True
            break
        time.sleep(1.0)

    if not appeared:
        raise RuntimeError(
            "Abaqus launcher returned rc=0, but no job files were created. "
            "Check %s" % submit_log
        )

    if os.path.isfile(sta_file):
        with open(sta_file, "r", encoding="utf-8", errors="ignore") as f:
            sta_txt = f.read().upper()

        if ("COMPLETED" not in sta_txt
                and "THE ANALYSIS HAS COMPLETED SUCCESSFULLY" not in sta_txt):
            raise RuntimeError(
                "Abaqus created STA but did not complete successfully. "
                "Check %s, %s, %s, %s" %
                (sta_file, msg_file, dat_file, submit_log)
            )
    else:
        created = []
        for fn in os.listdir(work_dir):
            if fn.startswith(job_name + "."):
                created.append(fn)
        created.sort()
        raise RuntimeError(
            "Abaqus launcher returned rc=0, but no STA file was created.\n"
            "Created files: %s\n"
            "Check %s" % (created, submit_log)
        )


def run_extract_odb(cfg):
    work_dir = os.path.abspath(cfg.WORK_DIR)
    script_path = os.path.join(work_dir, cfg.EXTRACT_SCRIPT)
    odb_path = os.path.join(work_dir, cfg.ODB_FILE)

    if not os.path.isfile(script_path):
        raise RuntimeError("Extract script not found: %s" % script_path)
    if not os.path.isfile(odb_path):
        raise RuntimeError("ODB file not found: %s" % odb_path)

    cmd = 'cmd /c ""%s" python "%s" "%s" "%s" "%s" "%s""' % (
        cfg.ABAQUS_CMD,
        cfg.EXTRACT_SCRIPT,
        cfg.ODB_FILE,
        cfg.RESP_CSV,
        cfg.SENS_CSV,
        cfg.CONFIG_JSON,
    )

    result = subprocess.run(
        cmd,
        cwd=work_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=True,
    )
    print(result.stdout)

    extract_log = os.path.join(work_dir, cfg.EXTRACT_LOG)
    ensure_parent_dir(extract_log)
    with open(extract_log, "w", encoding="utf-8", errors="ignore") as f:
        f.write(result.stdout)

    if result.returncode != 0:
        raise RuntimeError(
            "%s failed (rc=%d). See %s" %
            (cfg.EXTRACT_SCRIPT, result.returncode, extract_log)
        )


# =============================================================================
# CSV 读取 (与 extract_odb.py 输出格式对齐)
# =============================================================================
def read_selected_responses(path, response_specs):
    """
    response.csv 格式:
        name,type,component,label,integration_point,section_point,value
    按每条响应的 name 查值, 顺序与 response_specs 一致.
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise RuntimeError("Response CSV is empty: %s" % path)

    name_to_value = {}
    for row in rows:
        if "name" not in row or "value" not in row:
            raise RuntimeError(
                "Response CSV missing required columns 'name'/'value': %s" % path
            )
        name_to_value[str(row["name"]).strip()] = float(row["value"])

    out = []
    missing = []
    for spec in response_specs:
        name = str(spec["name"]).strip()
        if name not in name_to_value:
            missing.append(name)
            continue
        out.append(name_to_value[name])

    if missing:
        raise RuntimeError(
            "Responses not found in %s: %s" % (path, missing)
        )

    return np.array(out, dtype=float)


def read_sensitivity_csv(path, response_specs, param_names):
    """
    sensitivity.csv 矩阵格式 (由 extract_odb.py 输出):
        response_name,T1,T2,...,Tn

    同时向后兼容两种旧格式:
        (a) 单响应 : param_name,sens_value
        (b) 长表   : response_name,param_name,sens_value

    返回 shape = (n_resp, n_param) 的 numpy 数组.
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        raw_rows = list(reader)

    if not raw_rows:
        raise RuntimeError("Sensitivity CSV is empty: %s" % path)

    header = [str(x).strip() for x in raw_rows[0]]
    n_resp = len(response_specs)
    n_param = len(param_names)

    # --- (a) 单响应兼容 ---
    if header == ["param_name", "sens_value"]:
        if n_resp != 1:
            raise RuntimeError(
                "Single-response sensitivity.csv detected, "
                "but Config.RESPONSES has %d responses." % n_resp
            )
        sens = {}
        for row in raw_rows[1:]:
            if len(row) < 2:
                continue
            pname = row[0].strip().upper()
            sens[pname] = float(row[1])

        out = []
        missing = []
        for name in param_names:
            key = name.upper()
            if key in sens:
                out.append(sens[key])
            else:
                out.append(0.0)
                missing.append(name)
        if missing:
            print("[WARN] Missing sensitivities for %d parameters, filled with 0." %
                  len(missing))
        return np.array(out, dtype=float).reshape(1, -1)

    # --- 矩阵格式 (推荐) ---
    if len(header) >= 2 and header[0] == "response_name":
        csv_param_names = [x.strip().upper() for x in header[1:]]
        expected_param_names = [x.strip().upper() for x in param_names]

        if csv_param_names == expected_param_names:
            row_map = {}
            for row in raw_rows[1:]:
                if len(row) < 1:
                    continue
                rname = row[0].strip()
                vals = row[1:]
                if len(vals) != n_param:
                    raise RuntimeError(
                        "Row length mismatch for response %s in %s" % (rname, path)
                    )
                row_map[rname] = np.array([float(v) for v in vals], dtype=float)

            S = np.zeros((n_resp, n_param), dtype=float)
            for i, resp in enumerate(response_specs):
                rname = str(resp["name"]).strip()
                if rname not in row_map:
                    raise RuntimeError("Response %s not found in %s" % (rname, path))
                S[i, :] = row_map[rname]
            return S

    # --- (b) 长表兼容 ---
    dict_rows = []
    for row in raw_rows[1:]:
        if len(row) == 3:
            dict_rows.append({
                "response_name": row[0].strip(),
                "param_name":    row[1].strip(),
                "sens_value":    row[2].strip(),
            })
    if dict_rows:
        sens_map = {}
        for row in dict_rows:
            rname = row["response_name"]
            pname = row["param_name"].upper()
            sens_map[(rname, pname)] = float(row["sens_value"])

        S = np.zeros((n_resp, n_param), dtype=float)
        missing_count = 0
        for i, resp in enumerate(response_specs):
            rname = str(resp["name"]).strip()
            for j, pname in enumerate(param_names):
                key = (rname, pname.upper())
                if key in sens_map:
                    S[i, j] = sens_map[key]
                else:
                    S[i, j] = 0.0
                    missing_count += 1
        if missing_count > 0:
            print("[WARN] Missing %d sensitivity entries in long-table CSV, "
                  "filled with 0." % missing_count)
        return S

    raise RuntimeError("Unsupported sensitivity CSV format in %s." % path)


# =============================================================================
# Bayesian 相关数学
# =============================================================================
def build_normalized_sensitivity(raw_sens_mat, p_current, r_current, eps=1.0e-30):
    """
    归一化灵敏度:
        S_norm[i, j] = raw_sens[i, j] * p_j / r_i
    raw_sens_mat: (n_resp, n_param) 或 (n_param,)
    """
    raw_sens_mat = np.asarray(raw_sens_mat, dtype=float)
    p_current = np.asarray(p_current, dtype=float).reshape(-1)
    r_current = np.asarray(r_current, dtype=float).reshape(-1)

    if raw_sens_mat.ndim == 1:
        raw_sens_mat = raw_sens_mat.reshape(1, -1)

    if raw_sens_mat.shape[1] != p_current.size:
        raise ValueError("raw_sens_mat shape mismatch with p_current")
    if raw_sens_mat.shape[0] != r_current.size:
        raise ValueError("raw_sens_mat shape mismatch with r_current")

    if np.any(np.abs(r_current) < eps):
        raise RuntimeError("Analytical response too small for normalization.")

    return raw_sens_mat * p_current.reshape(1, -1) / r_current.reshape(-1, 1)


def build_normalized_residual(r_exp, r_ana, eps=1.0e-30):
    r_exp = np.asarray(r_exp, dtype=float).reshape(-1)
    r_ana = np.asarray(r_ana, dtype=float).reshape(-1)
    return (r_ana - r_exp) / np.maximum(np.abs(r_ana), eps)


def build_ccabs(r_exp, r_ana, r_scatter, eps=1.0e-30):
    r_exp = np.asarray(r_exp, dtype=float).reshape(-1)
    r_ana = np.asarray(r_ana, dtype=float).reshape(-1)

    r_scatter = np.asarray(r_scatter, dtype=float).reshape(-1)
    if r_scatter.size == 1:
        r_scatter = np.full(r_exp.size, r_scatter[0], dtype=float)

    if r_exp.size != r_ana.size:
        raise ValueError("r_exp and r_ana size mismatch")
    if r_scatter.size != r_exp.size:
        raise ValueError("r_scatter size mismatch with response size")

    rel_diff = (r_ana - r_exp) / np.maximum(np.abs(r_exp), eps)
    ccabs = np.sum(np.abs(rel_diff) / np.maximum(r_scatter, eps))
    return float(ccabs)

def build_ccmean(r_exp, r_ana, r_scatter, eps=1.0e-30):
    return _shared_build_ccmean(r_exp, r_ana, r_scatter, eps=eps)


def build_ccdis(r_exp, r_ana, r_scatter, eps=1.0e-30):
    return _shared_build_ccdis(r_exp, r_ana, r_scatter, eps=eps)

def build_cctot(ccabs, ccdis):
    return _shared_build_cctot(ccabs, ccdis)



def bayesian_update_normalized(
    p_current,
    S_norm,
    y,
    p_scatter,
    r_scatter,
    step_scale=1.0,
    damping=1.0e-12,
    eps=1.0e-30,
):
    p_current = np.asarray(p_current, dtype=float).reshape(-1)
    S_norm = np.asarray(S_norm, dtype=float)
    y = np.asarray(y, dtype=float).reshape(-1, 1)

    n_param = len(p_current)

    if S_norm.ndim != 2:
        raise ValueError("S_norm must be 2D")
    if S_norm.shape[1] != n_param:
        raise ValueError("S_norm shape mismatch with p_current")

    n_resp = S_norm.shape[0]
    if y.shape[0] != n_resp:
        raise ValueError("y size mismatch with number of responses")

    p_scatter = np.asarray(p_scatter, dtype=float).reshape(-1)
    r_scatter = np.asarray(r_scatter, dtype=float).reshape(-1)

    if p_scatter.size == 1:
        p_scatter = np.full(n_param, p_scatter[0], dtype=float)
    if r_scatter.size == 1:
        r_scatter = np.full(n_resp, r_scatter[0], dtype=float)

    if p_scatter.size != n_param:
        raise ValueError("p_scatter size mismatch")
    if r_scatter.size != n_resp:
        raise ValueError("r_scatter size mismatch")

    Dp = np.diag(p_current)

    Cp_n = 2.0 * np.diag(1.0 / np.maximum(p_scatter, eps) ** 2)
    Cr_n = np.diag(1.0 / np.maximum(r_scatter, eps) ** 2)

    Cp_n_eff = Cp_n + damping * np.eye(n_param)

    Cp_n_inv = np.linalg.inv(Cp_n_eff)
    Cr_n_inv = np.linalg.inv(Cr_n)

    G_n = Cp_n_inv @ S_norm.T @ np.linalg.inv(
        Cr_n_inv + S_norm @ Cp_n_inv @ S_norm.T
    )

    x_norm = step_scale * (G_n @ (-y))
    dp = Dp @ x_norm
    p_new = p_current.reshape(-1, 1) + dp

    return (
        p_new.reshape(-1),
        dp.reshape(-1),
        x_norm.reshape(-1),
        G_n,
        Cp_n,
        Cr_n,
    )


def apply_bounds_and_limit(p_old, p_new, cfg):
    p_old = np.asarray(p_old, dtype=float).reshape(-1)
    p_new = np.asarray(p_new, dtype=float).reshape(-1)

    if p_old.size != p_new.size:
        raise ValueError("p_old and p_new size mismatch")

    dp = p_new - p_old
    dp = np.clip(dp, -cfg.MAX_DELTA, cfg.MAX_DELTA)

    p_limited = p_old + dp
    p_limited = np.clip(p_limited, cfg.BOUNDS_LO, cfg.BOUNDS_HI)

    return p_limited


# =============================================================================
# 提交 + 提取 打包
# =============================================================================
def run_and_extract(cfg):
    run_abaqus_job(cfg)
    run_extract_odb(cfg)

    resp_csv = os.path.join(os.path.abspath(cfg.WORK_DIR), cfg.RESP_CSV)
    sens_csv = os.path.join(os.path.abspath(cfg.WORK_DIR), cfg.SENS_CSV)

    if not os.path.isfile(resp_csv):
        raise RuntimeError("Response CSV not found: %s" % resp_csv)
    if not os.path.isfile(sens_csv):
        raise RuntimeError("Sensitivity CSV not found: %s" % sens_csv)

    r_ana = read_selected_responses(resp_csv, cfg.RESPONSES)
    sens_raw = read_sensitivity_csv(sens_csv, cfg.RESPONSES, cfg.PARAM_NAMES)

    return r_ana, sens_raw


# =============================================================================
# 历史文件
# =============================================================================
def record_initial_state(cfg, p0, Ra0, sens_raw0, sens_norm0,
                         dR0, dR_norm0, gain_diag0, dx_raw0):
    sens_header = [
        "iter", "response_name", "param_name", "param_value", "response_value",
        "raw_sensitivity", "normalized_sensitivity",
        "response_diff", "response_diff_normalized",
        "gain_value", "dx_raw",
    ]

    response_names = get_response_names(cfg)
    Ra0 = np.asarray(Ra0, dtype=float).reshape(-1)
    dR0 = np.asarray(dR0, dtype=float).reshape(-1)
    dR_norm0 = np.asarray(dR_norm0, dtype=float).reshape(-1)
    sens_raw0 = np.asarray(sens_raw0, dtype=float)
    sens_norm0 = np.asarray(sens_norm0, dtype=float)
    gain_diag0 = np.asarray(gain_diag0, dtype=float).reshape(-1)
    dx_raw0 = np.asarray(dx_raw0, dtype=float).reshape(-1)

    for ir, rname in enumerate(response_names):
        for ip, pname in enumerate(cfg.PARAM_NAMES):
            append_csv_row(
                cfg.SENS_HISTORY_CSV,
                sens_header,
                [
                    0, rname, pname, p0[ip], Ra0[ir],
                    sens_raw0[ir, ip], sens_norm0[ir, ip],
                    dR0[ir], dR_norm0[ir],
                    gain_diag0[ip] if ip < len(gain_diag0) else np.nan,
                    dx_raw0[ip] if ip < len(dx_raw0) else np.nan,
                ],
            )

    param_header = [
        "iter", "param_name", "p_before", "dx_raw", "scale_used", "p_after", "dx_applied"
    ]

    for i, name in enumerate(cfg.PARAM_NAMES):
        append_csv_row(
            cfg.PARAM_HISTORY_CSV,
            param_header,
            [0, name, p0[i], 0.0, 0.0, p0[i], 0.0],
        )


def append_sensitivity_history(
    cfg, iteration, p_current, response_value,
    raw_sensitivity, normalized_sensitivity,
    response_diff, response_diff_normalized,
    gain_diag, dx_raw,
):
    header = [
        "iter", "response_name", "param_name", "param_value", "response_value",
        "raw_sensitivity", "normalized_sensitivity",
        "response_diff", "response_diff_normalized",
        "gain_value", "dx_raw",
    ]

    response_names = get_response_names(cfg)
    response_value = np.asarray(response_value, dtype=float).reshape(-1)
    raw_sensitivity = np.asarray(raw_sensitivity, dtype=float)
    normalized_sensitivity = np.asarray(normalized_sensitivity, dtype=float)
    response_diff = np.asarray(response_diff, dtype=float).reshape(-1)
    response_diff_normalized = np.asarray(response_diff_normalized, dtype=float).reshape(-1)
    gain_diag = np.asarray(gain_diag, dtype=float).reshape(-1)
    dx_raw = np.asarray(dx_raw, dtype=float).reshape(-1)

    for ir, rname in enumerate(response_names):
        for ip, pname in enumerate(cfg.PARAM_NAMES):
            append_csv_row(
                cfg.SENS_HISTORY_CSV,
                header,
                [
                    iteration, rname, pname,
                    p_current[ip], response_value[ir],
                    raw_sensitivity[ir, ip], normalized_sensitivity[ir, ip],
                    response_diff[ir], response_diff_normalized[ir],
                    gain_diag[ip] if ip < len(gain_diag) else np.nan,
                    dx_raw[ip] if ip < len(dx_raw) else np.nan,
                ],
            )


def append_param_history(cfg, iteration, p_before, dx_raw, scale_used, p_after, dx_applied):
    header = [
        "iter", "param_name", "p_before", "dx_raw", "scale_used", "p_after", "dx_applied"
    ]

    for i, name in enumerate(cfg.PARAM_NAMES):
        append_csv_row(
            cfg.PARAM_HISTORY_CSV,
            header,
            [
                iteration, name,
                p_before[i], dx_raw[i], scale_used,
                p_after[i], dx_applied[i],
            ],
        )


def append_trial_history(
    cfg, iteration, backtrack_id,
    p_before, dx_raw, trial_scale, p_trial,
    ra_trial, dR_trial, dR_norm_trial, res_trial, accepted,
):
    header = [
        "iter", "backtrack_id",
        "p_before_vec", "dx_raw_vec", "trial_scale", "p_trial_vec", "dx_applied_trial_vec",
        "ra_trial_vec", "dR_trial_vec", "dR_norm_trial_vec",
        "res_trial", "accepted",
    ]

    dx_applied_trial = p_trial - p_before

    append_csv_row(
        cfg.TRIAL_HISTORY_CSV,
        header,
        [
            iteration, backtrack_id,
            vec_to_text(p_before), vec_to_text(dx_raw),
            trial_scale,
            vec_to_text(p_trial), vec_to_text(dx_applied_trial),
            vec_to_text(ra_trial), vec_to_text(dR_trial), vec_to_text(dR_norm_trial),
            res_trial,
            int(bool(accepted)),
        ],
    )


def append_update_history(
    cfg, iteration, ra_vec, re_vec, dr_vec,
    ccabs, ccmean, ccdis, cctot, rel_res, dx_norm, max_abs_dparam,
):
    header = [
        "iter", "Ra_norm", "Re_norm", "dR_norm",
        "Ra_vec", "Re_vec", "dR_vec",
        "ccabs", "ccmean", "ccdis", "cctot",
        "rel_res", "dx_norm", "max_abs_dparam",
    ]

    ra_vec = np.asarray(ra_vec, dtype=float).reshape(-1)
    re_vec = np.asarray(re_vec, dtype=float).reshape(-1)
    dr_vec = np.asarray(dr_vec, dtype=float).reshape(-1)

    row = [
        iteration,
        float(np.linalg.norm(ra_vec)),
        float(np.linalg.norm(re_vec)),
        float(np.linalg.norm(dr_vec)),
        vec_to_text(ra_vec), vec_to_text(re_vec), vec_to_text(dr_vec),
        float(ccabs), float(ccmean), float(ccdis), float(cctot),
        float(rel_res), float(dx_norm), float(max_abs_dparam),
    ]

    append_csv_row(cfg.HISTORY_CSV, header, row)


# =============================================================================
# 绘图
# =============================================================================
def plot_response_history_from_hist(cfg, hist):
    if not hist:
        return
    x = [row["iter"] for row in hist]
    y_ra = [row["Ra_norm"] for row in hist]
    y_re = [row["Re_norm"] for row in hist]

    ensure_parent_dir(cfg.RESPONSE_HISTORY_PNG)
    plt.figure(figsize=(8, 5))
    plt.plot(x, y_ra, marker="o", label="Analytical response norm")
    plt.plot(x, y_re, marker="s", label="Experimental response norm")
    plt.xlabel("Iteration")
    plt.ylabel("Response norm")
    plt.title("Response history")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(cfg.RESPONSE_HISTORY_PNG, dpi=200)
    plt.close()


def plot_residual_history_from_hist(cfg, hist):
    if not hist:
        return
    x = [row["iter"] for row in hist]
    y_abs = [row["dR_norm"] for row in hist]
    y_rel = [row["rel_res"] for row in hist]

    ensure_parent_dir(cfg.RESIDUAL_HISTORY_PNG)
    plt.figure(figsize=(8, 5))
    plt.semilogy(x, y_abs, marker="o", label="||dR||_2")
    plt.semilogy(x, y_rel, marker="s", label="Relative residual")
    plt.xlabel("Iteration")
    plt.ylabel("Residual")
    plt.title("Residual history")
    plt.grid(True, which="both")
    plt.legend()
    plt.tight_layout()
    plt.savefig(cfg.RESIDUAL_HISTORY_PNG, dpi=200)
    plt.close()


def plot_parameter_update_norm_history(cfg, hist):
    if not hist:
        return
    x = [row["iter"] for row in hist]
    y = [row["dx_norm"] for row in hist]

    ensure_parent_dir(cfg.PARAM_NORM_HISTORY_PNG)
    plt.figure(figsize=(8, 5))
    plt.semilogy(x, y, marker="o")
    plt.xlabel("Iteration")
    plt.ylabel("||dx_applied||_2")
    plt.title("Parameter update norm history")
    plt.grid(True, which="both")
    plt.tight_layout()
    plt.savefig(cfg.PARAM_NORM_HISTORY_PNG, dpi=200)
    plt.close()


# =============================================================================
# main
# =============================================================================
def main():
    import sys
    if len(sys.argv) < 2:
        raise RuntimeError(
            "Usage: python updateModelByJson.py <config_json>"
        )

    config_json = sys.argv[1]
    cfg = Config(config_json)
    work_dir = os.path.abspath(cfg.WORK_DIR)

    # 清理/准备输出
    ensure_parent_dir(cfg.LOG_FILE)
    ensure_parent_dir(cfg.HISTORY_CSV)
    ensure_parent_dir(cfg.PARAM_HISTORY_CSV)
    ensure_parent_dir(cfg.SENS_HISTORY_CSV)
    ensure_parent_dir(cfg.TRIAL_HISTORY_CSV)

    for p in (
        cfg.HISTORY_CSV,
        cfg.PARAM_HISTORY_CSV,
        cfg.SENS_HISTORY_CSV,
        cfg.TRIAL_HISTORY_CSV,
        cfg.SUBMIT_LOG,
        cfg.EXTRACT_LOG,
        cfg.RESPONSE_HISTORY_PNG,
        cfg.RESIDUAL_HISTORY_PNG,
        cfg.PARAM_NORM_HISTORY_PNG,
    ):
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass

    log_file = open(cfg.LOG_FILE, "w", encoding="utf-8")

    def log(msg):
        print(msg)
        log_file.write(msg + "\n")
        log_file.flush()

    r_exp = get_exp_response_vector(cfg)
    r_scatter_vec = get_response_scatter_vector(cfg)

    log("=" * 72)
    log("MODEL UPDATE / BAYESIAN PARAMETER IDENTIFICATION")
    log("=" * 72)
    log("CONFIG_JSON       : %s" % cfg.CONFIG_JSON)
    log("WORK_DIR          : %s" % work_dir)
    log("Main INP          : %s" % cfg.INP_FILE)
    log("Parameter file    : %s" % cfg.PARAMETER_FILE)
    log("Job               : %s" % cfg.JOB_NAME)
    log("N_PARAM           : %d" % cfg.N_PARAM)
    log("N_RESP            : %d" % len(cfg.RESPONSES))
    log("Response names    : %s" % ", ".join(get_response_names(cfg)))

    type_counts = {"displacement": 0, "stress": 0}
    for rsp in cfg.RESPONSES:
        type_counts[rsp["type"]] = type_counts.get(rsp["type"], 0) + 1
    log("Response types    : disp=%d, stress=%d" %
        (type_counts.get("displacement", 0), type_counts.get("stress", 0)))
    log("Target response   : %s" % fmt_vec_log(r_exp))
    log("TOL_CCABS         : %.10e" % cfg.TOL_CCABS)
    log("TOL_REL_RES       : %.10e" % cfg.TOL_REL_RES)
    log("TOL_MAX_ABS_DPARAM: %.10e" % cfg.TOL_MAX_ABS_DPARAM)
    log("")

    main_inp_path = os.path.join(work_dir, cfg.INP_FILE)
    parameter_inp_path = os.path.join(work_dir, cfg.PARAMETER_FILE)

    if not os.path.isfile(main_inp_path):
        raise RuntimeError("Main INP file not found: %s" % main_inp_path)
    if not os.path.isfile(parameter_inp_path):
        raise RuntimeError("Parameter INP file not found: %s" % parameter_inp_path)

    # 初始参数
    p = build_initial_parameter_vector(cfg)
    update_parameters_in_parameter_inp(parameter_inp_path, cfg.PARAM_NAMES, p)

    hist = []
    stall_count = 0
    scale = cfg.SCALE_INIT

    # --------- 初始分析 ---------
    Ra0, sens_raw0 = run_and_extract(cfg)
    dR0_vec = r_exp - Ra0
    res0 = float(np.linalg.norm(dR0_vec))
    rel_res0 = res0 / (float(np.linalg.norm(r_exp)) + cfg.EPS)

    ccabs0 = build_ccabs(
        r_exp=r_exp, r_ana=Ra0,
        r_scatter=r_scatter_vec, eps=cfg.EPS,
    )
    ccmean0 = build_ccmean(
        r_exp=r_exp, r_ana=Ra0,
        r_scatter=r_scatter_vec, eps=cfg.EPS,
    )
    ccdis0 = build_ccdis(
        r_exp=r_exp, r_ana=Ra0,
        r_scatter=r_scatter_vec, eps=cfg.EPS,
    )
    cctot0 = build_cctot(ccabs0, ccdis0)

    sens_norm0 = build_normalized_sensitivity(
        raw_sens_mat=sens_raw0,
        p_current=p, r_current=Ra0, eps=cfg.EPS,
    )

    y0 = build_normalized_residual(
        r_exp=r_exp, r_ana=Ra0, eps=cfg.EPS,
    )

    _, dp_vec0, _, G_n0, _, _ = bayesian_update_normalized(
        p_current=p,
        S_norm=sens_norm0,
        y=y0,
        p_scatter=np.array([cfg.P_SCATTER], dtype=float),
        r_scatter=r_scatter_vec,
        step_scale=scale,
        damping=cfg.DAMPING,
        eps=cfg.EPS,
    )

    dx_raw0 = dp_vec0
    gain_diag0 = (
        np.diag(G_n0 @ sens_norm0)
        if G_n0.shape[1] == sens_norm0.shape[0]
        else np.full(cfg.N_PARAM, np.nan)
    )
    dR_norm0 = y0

    record_initial_state(
        cfg=cfg, p0=p, Ra0=Ra0,
        sens_raw0=sens_raw0, sens_norm0=sens_norm0,
        dR0=dR0_vec, dR_norm0=dR_norm0,
        gain_diag0=gain_diag0, dx_raw0=dx_raw0,
    )

    hist.append({
        "iter": 0,
        "Ra_norm": float(np.linalg.norm(Ra0)),
        "Re_norm": float(np.linalg.norm(r_exp)),
        "dR_norm": res0,
        "Ra_vec": vec_to_text(Ra0),
        "Re_vec": vec_to_text(r_exp),
        "dR_vec": vec_to_text(dR0_vec),
        "ccabs": ccabs0,
        "ccmean": ccmean0,
        "ccdis": ccdis0,
        "cctot": cctot0,
        "rel_res": rel_res0,
        "dx_norm": 0.0,
        "max_abs_dparam": 0.0,
    })

    append_update_history(
        cfg=cfg, iteration=0,
        ra_vec=Ra0, re_vec=r_exp, dr_vec=dR0_vec,
        ccabs=ccabs0, ccmean=ccmean0, ccdis=ccdis0, cctot=cctot0,
        rel_res=rel_res0,
        dx_norm=0.0, max_abs_dparam=0.0,
    )

    log("INITIAL STATE (iter=0)")
    log("Ra              : %s" % fmt_vec_log(Ra0))
    log("Re              : %s" % fmt_vec_log(r_exp))
    log("dR              : %s" % fmt_vec_log(dR0_vec))
    log("CCABS           : %.10e" % ccabs0)
    log("CCMEAN          : %.10e" % ccmean0)
    log("CCDIS           : %.10e" % ccdis0)
    log("CCTOT           : %.10e" % cctot0)
    log("rel_res         : %.10e" % rel_res0)
    log("||sens_raw||_F  : %.10e" % np.linalg.norm(sens_raw0))
    log("||sens_norm||_F : %.10e" % np.linalg.norm(sens_norm0))
    log("||dx_raw||_2    : %.10e" % np.linalg.norm(dx_raw0))

    best_Ra_final = Ra0.copy()

    # --------- 迭代 ---------
    for it in range(1, cfg.MAX_ITERS + 1):
        log("")
        log("=" * 72)
        log("ITERATION %d | scale = %.6g" % (it, scale))
        log("=" * 72)

        Ra, sens_raw = run_and_extract(cfg)

        dR_vec = r_exp - Ra
        res = float(np.linalg.norm(dR_vec))
        rel_res = res / (float(np.linalg.norm(r_exp)) + cfg.EPS)

        sens_norm = build_normalized_sensitivity(
            raw_sens_mat=sens_raw,
            p_current=p, r_current=Ra, eps=cfg.EPS,
        )

        y = build_normalized_residual(
            r_exp=r_exp, r_ana=Ra, eps=cfg.EPS,
        )

        _, dp_vec, _, G_n, _, _ = bayesian_update_normalized(
            p_current=p,
            S_norm=sens_norm,
            y=y,
            p_scatter=np.array([cfg.P_SCATTER], dtype=float),
            r_scatter=r_scatter_vec,
            step_scale=scale,
            damping=cfg.DAMPING,
            eps=cfg.EPS,
        )

        dx_raw = dp_vec
        gain_diag = (
            np.diag(G_n @ sens_norm)
            if G_n.shape[1] == sens_norm.shape[0]
            else np.full(cfg.N_PARAM, np.nan)
        )
        dR_norm = y

        append_sensitivity_history(
            cfg=cfg, iteration=it, p_current=p,
            response_value=Ra,
            raw_sensitivity=sens_raw,
            normalized_sensitivity=sens_norm,
            response_diff=dR_vec,
            response_diff_normalized=dR_norm,
            gain_diag=gain_diag, dx_raw=dx_raw,
        )

        log("Ra              : %s" % fmt_vec_log(Ra))
        log("Re              : %s" % fmt_vec_log(r_exp))
        log("dR              : %s" % fmt_vec_log(dR_vec))
        log("rel_res         : %.10e" % rel_res)
        log("||sens_raw||_F  : %.10e" % np.linalg.norm(sens_raw))
        log("||sens_norm||_F : %.10e" % np.linalg.norm(sens_norm))
        log("||dx_raw||_2    : %.10e" % np.linalg.norm(dx_raw))

        # ---- 回溯线搜索 ----
        base_res = res
        best_p = None
        best_res = None
        best_scale = None
        best_Ra = None
        best_dR_vec = None
        best_rel_res = None

        trial_scale = 1.0

        for bt in range(cfg.MAX_BACKTRACK + 1):
            p_trial_candidate = p + dx_raw * trial_scale
            p_trial = apply_bounds_and_limit(p, p_trial_candidate, cfg)
            dx_applied_trial = p_trial - p

            if np.max(np.abs(dx_applied_trial)) < cfg.TOL_DX:
                best_p = p_trial
                best_res = base_res
                best_scale = trial_scale
                best_Ra = Ra
                best_dR_vec = dR_vec
                best_rel_res = rel_res

                append_trial_history(
                    cfg=cfg, iteration=it, backtrack_id=bt,
                    p_before=p, dx_raw=dx_raw, trial_scale=trial_scale,
                    p_trial=p_trial, ra_trial=Ra,
                    dR_trial=dR_vec,
                    dR_norm_trial=build_normalized_residual(r_exp, Ra, eps=cfg.EPS),
                    res_trial=base_res, accepted=True,
                )
                log("  [BT %d] dx ~ 0, skip trial." % bt)
                break

            update_parameters_in_parameter_inp(parameter_inp_path, cfg.PARAM_NAMES, p_trial)
            Ra_trial, _ = run_and_extract(cfg)

            dR_trial_vec = r_exp - Ra_trial
            res_trial = float(np.linalg.norm(dR_trial_vec))
            dR_norm_trial = build_normalized_residual(
                r_exp=r_exp, r_ana=Ra_trial, eps=cfg.EPS,
            )

            accepted = (res_trial < base_res * (1.0 - 1e-6))

            append_trial_history(
                cfg=cfg, iteration=it, backtrack_id=bt,
                p_before=p, dx_raw=dx_raw, trial_scale=trial_scale,
                p_trial=p_trial, ra_trial=Ra_trial,
                dR_trial=dR_trial_vec, dR_norm_trial=dR_norm_trial,
                res_trial=res_trial, accepted=accepted,
            )

            if best_res is None or res_trial < best_res:
                best_p = p_trial
                best_res = res_trial
                best_scale = trial_scale
                best_Ra = Ra_trial
                best_dR_vec = dR_trial_vec
                best_rel_res = res_trial / (float(np.linalg.norm(r_exp)) + cfg.EPS)

            if accepted:
                log("  [BT %d] scale=%.4g  res=%.10e  ACCEPT" %
                    (bt, trial_scale, res_trial))
                break
            else:
                log("  [BT %d] scale=%.4g  res=%.10e  shrink" %
                    (bt, trial_scale, res_trial))
                trial_scale = max(cfg.SCALE_MIN, trial_scale * cfg.SCALE_SHRINK)

        if best_p is None:
            best_p = p.copy()
            best_res = base_res
            best_scale = 0.0
            best_Ra = Ra
            best_dR_vec = dR_vec
            best_rel_res = rel_res

        update_parameters_in_parameter_inp(parameter_inp_path, cfg.PARAM_NAMES, best_p)

        dx_applied = best_p - p
        improve = (base_res - best_res) / (base_res + cfg.EPS)

        ccabs = build_ccabs(
            r_exp=r_exp, r_ana=best_Ra,
            r_scatter=r_scatter_vec, eps=cfg.EPS,
        )
        ccmean = build_ccmean(
            r_exp=r_exp, r_ana=best_Ra,
            r_scatter=r_scatter_vec, eps=cfg.EPS,
        )
        ccdis = build_ccdis(
            r_exp=r_exp, r_ana=best_Ra,
            r_scatter=r_scatter_vec, eps=cfg.EPS,
        )
        cctot = build_cctot(ccabs, ccdis)

        max_abs_dparam = float(np.max(np.abs(dx_applied)))
        dx_norm = float(np.linalg.norm(dx_applied))

        append_param_history(
            cfg=cfg, iteration=it,
            p_before=p, dx_raw=dx_raw,
            scale_used=best_scale,
            p_after=best_p, dx_applied=dx_applied,
        )

        log("CCABS            : %.10e" % ccabs)
        log("CCMEAN           : %.10e" % ccmean)
        log("CCDIS            : %.10e" % ccdis)
        log("CCTOT            : %.10e" % cctot)
        log("max|dx_applied|  : %.10e" % max_abs_dparam)
        log("||dx_applied||_2 : %.10e" % dx_norm)
        log("res before       : %.10e" % base_res)
        log("res after        : %.10e" % best_res)
        log("improve          : %.6f%%" % (improve * 100.0))

        hist.append({
            "iter": it,
            "Ra_norm": float(np.linalg.norm(best_Ra)),
            "Re_norm": float(np.linalg.norm(r_exp)),
            "dR_norm": float(np.linalg.norm(best_dR_vec)),
            "Ra_vec": vec_to_text(best_Ra),
            "Re_vec": vec_to_text(r_exp),
            "dR_vec": vec_to_text(best_dR_vec),
            "ccabs": ccabs,
            "ccmean": ccmean,
            "ccdis": ccdis,
            "cctot": cctot,
            "rel_res": best_rel_res,
            "dx_norm": dx_norm,
            "max_abs_dparam": max_abs_dparam,
        })

        append_update_history(
            cfg=cfg, iteration=it,
            ra_vec=best_Ra, re_vec=r_exp, dr_vec=best_dR_vec,
            ccabs=ccabs, ccmean=ccmean, ccdis=ccdis, cctot=cctot,
            rel_res=best_rel_res,
            dx_norm=dx_norm, max_abs_dparam=max_abs_dparam,
        )

        if it >= cfg.MIN_ITERS:
            if improve < cfg.TOL_REL_IMPROVE:
                stall_count += 1
            else:
                stall_count = 0

        if improve > 1e-3:
            scale = min(cfg.SCALE_MAX, scale * cfg.SCALE_GROW)
        else:
            scale = max(cfg.SCALE_MIN, scale)

        p = best_p.copy()
        best_Ra_final = best_Ra.copy()

        if it >= cfg.MIN_ITERS and ccabs < cfg.TOL_CCABS:
            log(">>> Converged: CCABS < TOL_CCABS")
            break
        if it >= cfg.MIN_ITERS and best_rel_res < cfg.TOL_REL_RES:
            log(">>> Converged: relative residual < TOL_REL_RES")
            break
        if it >= cfg.MIN_ITERS and max_abs_dparam < cfg.TOL_MAX_ABS_DPARAM:
            log(">>> Converged: max|dx_applied| < TOL_MAX_ABS_DPARAM")
            break
        if it >= cfg.MIN_ITERS and stall_count >= cfg.STALL_LIMIT:
            log(">>> Converged: stalled for %d iterations" % cfg.STALL_LIMIT)
            break

    # --------- 收尾 ---------
    hist_path = os.path.join(work_dir, cfg.HISTORY_CSV)
    ensure_parent_dir(hist_path)
    if hist:
        plot_response_history_from_hist(cfg, hist)
        plot_residual_history_from_hist(cfg, hist)
        plot_parameter_update_norm_history(cfg, hist)

    log("")
    log("=" * 72)
    log("Final mean parameter = %.10e" % np.mean(p))
    log("Final response       = %s" % fmt_vec_log(best_Ra_final))
    log("Summary  = %s" % hist_path)
    log("Sens CSV = %s" % os.path.join(work_dir, cfg.SENS_HISTORY_CSV))
    log("Param CSV= %s" % os.path.join(work_dir, cfg.PARAM_HISTORY_CSV))
    log("Trial CSV= %s" % os.path.join(work_dir, cfg.TRIAL_HISTORY_CSV))
    log("Done.")
    log_file.close()


if __name__ == "__main__":
    main()
