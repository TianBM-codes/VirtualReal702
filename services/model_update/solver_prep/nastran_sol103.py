from pathlib import Path

SKIP_CARDS = {
    "EIGB",
    "EIGC",
    "EIGR",
    "EIGRL",
}

SKIP_PARAM_NAMES = {
    "POST",
    "K6ROT",
    "GRDPNT",
    "COUPMASS",
}


def read_lines(path):
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return Path(path).read_text(encoding="latin-1").splitlines()


def write_lines(path, lines):
    text = "\n".join(lines) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def find_begin_bulk(lines):
    for i, line in enumerate(lines):
        if line.strip().upper() == "BEGIN BULK":
            return i
    raise ValueError("未找到 BEGIN BULK 段")


def split_bdf(lines):
    idx = find_begin_bulk(lines)
    return lines[:idx], lines[idx + 1:]


def normalize_card_name(line):
    stripped = line.lstrip()
    if not stripped or stripped.startswith("$"):
        return ""
    token = stripped.split(",", 1)[0].split()[0]
    return token.rstrip("*").upper()


def parse_param_name(line):
    stripped = line.strip()
    if not stripped:
        return ""

    if stripped.upper().startswith("PARAM,"):
        parts = [p.strip().upper() for p in stripped.split(",")]
        if len(parts) >= 2:
            return parts[1]

    parts = stripped.split()
    if len(parts) >= 2 and parts[0].upper() == "PARAM":
        return parts[1].upper()

    return ""


def is_continuation_line(line):
    stripped = line.lstrip()
    if not stripped:
        return False
    return stripped[0] in {"+", "*", ","}


def format_float_like_bas(value):
    """
    Approximate the VB formatting used in nastran.bas.
    """
    if isinstance(value, int):
        return str(value)

    if value == 0:
        return "0."

    if abs(value) >= 1.0e5 or abs(value) < 1.0e-2:
        return "{:.2E}".format(value).replace("E+0", "E+").replace("E-0", "E-")
    else:
        text = "{:.1f}".format(value)
        return text


def resolve_dynamic_norm(value):
    raw = value if value is not None else 2
    if isinstance(raw, str):
        token = raw.strip().upper()
        if not token:
            return "MASS"
        if token in {"1", "MAX"}:
            return "MAX"
        if token in {"2", "MASS"}:
            return "MASS"
        raise ValueError("dynamic.norm must be one of: 1, 2, MAX, MASS")

    norm = int(raw)
    if norm == 1:
        return "MAX"
    if norm == 2:
        return "MASS"
    raise ValueError("dynamic.norm must be 1 or 2")


def resolve_result_target(value):
    token = str(value or "OP2").strip().upper()
    if token in {"OP2", "F06", "BOTH"}:
        return token
    raise ValueError("result.target must be one of: OP2, F06, BOTH")


def resolve_post_value(settings, result_target="OP2", default_post=None):
    if "post" in settings and settings.get("post") is not None:
        return int(settings.get("post"))

    embed_geometry = settings.get("embed_geometry")
    if embed_geometry is not None:
        if str(result_target).upper() == "F06":
            return -1
        return -1 if bool(embed_geometry) else -2

    if default_post is not None:
        return int(default_post)

    if str(result_target).upper() == "F06":
        return -1
    return -1


def build_displacement_request(displacement, result_target):
    target = resolve_result_target(result_target)
    rhs = str(displacement or "ALL").strip() or "ALL"
    if target == "OP2":
        return f"DISPLACEMENT(PLOT) = {rhs}"
    if target == "BOTH":
        return f"DISPLACEMENT(PLOT,PRINT) = {rhs}"
    return f"DISPLACEMENT = {rhs}"


def build_eigrl_fields(settings):
    sid = 1

    fmin = float(settings.get("dynamic.fmin", 0.0))
    fmax_raw = settings.get("dynamic.fmax", 1.0e9)
    vectors_raw = settings.get("dynamic.vectors", 10)
    normalization = resolve_dynamic_norm(settings.get("dynamic.norm", 2))
    size = int(settings.get("dynamic.size", 0))

    v1 = format_float_like_bas(fmin)
    fmax = None if fmax_raw in (None, "") else float(fmax_raw)
    vectors = None if vectors_raw in (None, "") else int(vectors_raw)

    if fmax is None or fmax <= 0:
        v2 = ""
    else:
        v2 = format_float_like_bas(fmax)

    if vectors is None or vectors <= 0:
        nd = ""
    else:
        nd = str(vectors)

    if size > 0:
        maxset = str(size)
    else:
        maxset = ""

    return {
        "sid": str(sid),
        "v1": v1,
        "v2": v2,
        "nd": nd,
        "maxset": maxset,
        "shfscl": "",
        "normalization": normalization,
    }


def _split_bdf_fields(line):
    stripped = str(line or "").rstrip("\n")
    if "," in stripped:
        return [part.strip() for part in stripped.split(",")]
    chunks = [stripped[i:i + 8].strip() for i in range(0, len(stripped), 8)]
    while chunks and chunks[-1] == "":
        chunks.pop()
    return chunks


def _parse_eigrl_card(line):
    fields = _split_bdf_fields(line)
    if not fields:
        return None
    if str(fields[0]).strip().upper() != "EIGRL":
        return None

    def _field(index):
        return str(fields[index]).strip() if index < len(fields) else ""

    return {
        "sid": _field(1) or "1",
        "v1": _field(2),
        "v2": _field(3),
        "nd": _field(4),
        "maxset": _field(6),
        "shfscl": _field(7),
        "normalization": _field(8) or "MASS",
    }


def _find_existing_eigrl_fields(bulk_lines):
    for line in list(bulk_lines or []):
        card = normalize_card_name(line)
        if card != "EIGRL":
            continue
        parsed = _parse_eigrl_card(line)
        if parsed is not None:
            return parsed
    return None


def _resolve_sol103_eigrl_fields(settings, *, bulk_lines=None):
    existing = _find_existing_eigrl_fields(list(bulk_lines or []))
    if existing is None:
        return build_eigrl_fields(settings)

    resolved = dict(existing)
    if "dynamic.sid" in settings and settings.get("dynamic.sid") not in (None, ""):
        resolved["sid"] = str(int(settings.get("dynamic.sid")))
    if "dynamic.fmin" in settings and settings.get("dynamic.fmin") not in (None, ""):
        resolved["v1"] = format_float_like_bas(float(settings.get("dynamic.fmin")))
    if "dynamic.fmax" in settings:
        fmax_raw = settings.get("dynamic.fmax")
        if fmax_raw in (None, ""):
            resolved["v2"] = ""
        else:
            fmax = float(fmax_raw)
            resolved["v2"] = "" if fmax <= 0 else format_float_like_bas(fmax)
    if "dynamic.vectors" in settings:
        vectors_raw = settings.get("dynamic.vectors")
        if vectors_raw in (None, ""):
            resolved["nd"] = ""
        else:
            vectors = int(vectors_raw)
            resolved["nd"] = "" if vectors <= 0 else str(vectors)
    if "dynamic.size" in settings:
        size_raw = settings.get("dynamic.size")
        size = 0 if size_raw in (None, "") else int(size_raw)
        resolved["maxset"] = str(size) if size > 0 else ""
    if "dynamic.norm" in settings and settings.get("dynamic.norm") not in (None, ""):
        resolved["normalization"] = resolve_dynamic_norm(settings.get("dynamic.norm", 2))
    else:
        resolved["normalization"] = resolve_dynamic_norm(resolved.get("normalization", "MASS"))
    return resolved


def extract_sol103_settings_from_bdf(input_bdf):
    lines = read_lines(input_bdf)
    _, bulk_lines = split_bdf(lines)
    eigrl = _find_existing_eigrl_fields(bulk_lines)
    if eigrl is None:
        return {}
    settings = {}
    if eigrl.get("sid"):
        settings["dynamic.sid"] = int(eigrl["sid"])
    if eigrl.get("v1") not in (None, ""):
        settings["dynamic.fmin"] = float(str(eigrl["v1"]).replace("D", "E"))
    if eigrl.get("v2") not in (None, ""):
        settings["dynamic.fmax"] = float(str(eigrl["v2"]).replace("D", "E"))
    if eigrl.get("nd") not in (None, ""):
        settings["dynamic.vectors"] = int(eigrl["nd"])
    if eigrl.get("maxset") not in (None, ""):
        settings["dynamic.size"] = int(eigrl["maxset"])
    if eigrl.get("normalization") not in (None, ""):
        settings["dynamic.norm"] = resolve_dynamic_norm(eigrl["normalization"])
    return settings


def build_sol103_controls(settings, *, bulk_lines=None):
    eigrl = _resolve_sol103_eigrl_fields(settings, bulk_lines=bulk_lines)

    echo = settings.get("echo", "NONE")
    displacement = settings.get("displacement", "ALL")
    result_target = resolve_result_target(settings.get("result.target", "OP2"))
    displacement_line = build_displacement_request(displacement, result_target)
    post = resolve_post_value(settings, result_target=result_target, default_post=-1)
    grdpnt = int(settings.get("grdpnt", 0))

    k6rot = float(settings.get("fem.k6rot", -1.0))
    if k6rot < 0:
        k6rot_text = "10.0"
    else:
        k6rot_text = format_float_like_bas(k6rot)

    lumped = bool(settings.get("compute.lumped", True))
    coupmass = "-1" if lumped else "1"

    dynamic_master = bool(settings.get("dynamic.master", False))
    mdof_count = int(settings.get("fem.mdof.count", 0))
    has_bailout = dynamic_master and mdof_count != 0

    lines = [
        "SOL 103",
        "CEND",
        "METHOD = {}".format(eigrl["sid"]),
        "ECHO={}".format(echo),
        displacement_line,
        "BEGIN BULK",
        "PARAM   POST          {}".format(post),
        "PARAM   GRDPNT         {}".format(grdpnt),
        "PARAM   K6ROT   {}".format(k6rot_text),
        "PARAM   COUPMASS      {}".format(coupmass),
    ]

    if has_bailout:
        lines.append("PARAM   BAILOUT       -1")

    eigrl_line = "EIGRL   {sid:>8}{v1:>8}{v2:>8}{nd:>8}{blank:>8}{maxset:>8}{shfscl:>8}{norm:>8}".format(
        sid=eigrl["sid"],
        v1=eigrl["v1"],
        v2=eigrl["v2"],
        nd=eigrl["nd"],
        blank="",
        maxset=eigrl["maxset"],
        shfscl=eigrl["shfscl"],
        norm=eigrl["normalization"],
    )
    lines.append(eigrl_line)

    return lines, has_bailout


def filter_bulk_lines(bulk_lines, has_bailout):
    filtered = []
    skipping_continuation = False

    for line in bulk_lines:
        stripped = line.lstrip()

        if not stripped:
            filtered.append(line)
            skipping_continuation = False
            continue

        if stripped.startswith("$"):
            filtered.append(line)
            continue

        if skipping_continuation and is_continuation_line(line):
            continue

        card = normalize_card_name(line)

        if card in SKIP_CARDS:
            skipping_continuation = True
            continue

        if card == "PARAM":
            pname = parse_param_name(line)
            if pname in SKIP_PARAM_NAMES:
                skipping_continuation = True
                continue
            if pname == "BAILOUT" and has_bailout:
                skipping_continuation = True
                continue

        skipping_continuation = False
        filtered.append(line)

    return filtered


def convert_to_sol103(input_bdf, output_bdf, settings):
    lines = read_lines(input_bdf)
    _, bulk_lines = split_bdf(lines)

    controls, has_bailout = build_sol103_controls(settings, bulk_lines=bulk_lines)
    filtered_bulk = filter_bulk_lines(bulk_lines, has_bailout)

    output_lines = controls + filtered_bulk
    write_lines(output_bdf, output_lines)


if __name__ == "__main__":
    settings = {
        "dynamic.fmin": 100.0,
        "dynamic.fmax": 1.0e9,
        "dynamic.vectors": 12,
        "dynamic.norm": 2,
        "dynamic.size": 0,
        "compute.lumped": True,
        "fem.k6rot": -1.0,
        "dynamic.master": True,
        "fem.mdof.count": 1,
        "echo": "NONE",
        "displacement": "ALL",
        "post": -5,
        "grdpnt": 0,
    }

    convert_to_sol103(
        input_bdf="D:\WorkSpace\WebThreeJS\PyModelToJson\VirtualReal702\model\engine.bdf",
        output_bdf="D:\WorkSpace\WebThreeJS\PyModelToJson\VirtualReal702\model\output_sol103.bdf",
        settings=settings,
    )
