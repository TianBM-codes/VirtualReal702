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


def build_eigrl_fields(settings):
    sid = 1

    fmin = float(settings.get("dynamic.fmin", 0.0))
    fmax = float(settings.get("dynamic.fmax", 1.0e9))
    vectors = int(settings.get("dynamic.vectors", 10))
    normalization = resolve_dynamic_norm(settings.get("dynamic.norm", 2))
    size = int(settings.get("dynamic.size", 0))

    v1 = format_float_like_bas(fmin)

    if fmax > 1.0e6:
        v2 = ""
        nd = str(vectors)
    else:
        v2 = format_float_like_bas(fmax)
        nd = ""

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


def build_sol103_controls(settings):
    eigrl = build_eigrl_fields(settings)

    echo = settings.get("echo", "NONE")
    displacement = settings.get("displacement", "ALL")
    post = int(settings.get("post", -5))
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
        "DISPLACEMENT = {}".format(displacement),
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

    controls, has_bailout = build_sol103_controls(settings)
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
