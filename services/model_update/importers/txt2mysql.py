import json
import math

from db import clear_unv_tables, ensure_tables_exist, get_connection
from services.model_update.analysis.project_config_service import save_test_model_dimensions


NODES_TXT = "nodes.txt"
LINES_TXT = "lines.txt"
MODES_TXT = "modes.txt"

PROJECT_ID = 1
FILE_ID = 0
CLEAR_BEFORE_INSERT = True

PHASE_UNIT = "deg"
REAL_EXPORT_MODE = "real_part"


def clean_line(line):
    return line.split("#")[0].strip()


def split_values(line):
    return line.replace(",", " ").split()


def read_nodes(filename):
    """
    Format:
        node_id x y z
    """
    nodes = {}

    with open(filename, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = clean_line(raw_line)
            if not line:
                continue

            parts = split_values(line)
            if len(parts) < 4:
                raise ValueError(f"Invalid node row: {raw_line.strip()}")

            node_id = int(parts[0])
            x = float(parts[1])
            y = float(parts[2])
            z = float(parts[3])
            nodes[node_id] = (x, y, z)

    return nodes


def read_lines(filename):
    """
    Format:
        node1 node2
    """
    lines = []

    with open(filename, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = clean_line(raw_line)
            if not line:
                continue

            parts = split_values(line)
            if len(parts) < 2:
                raise ValueError(f"Invalid element row: {raw_line.strip()}")

            n1 = int(parts[0])
            n2 = int(parts[1])
            lines.append((n1, n2))

    return lines


def phase_to_rad(phase):
    if PHASE_UNIT == "deg":
        return math.radians(phase)
    if PHASE_UNIT == "rad":
        return phase
    raise ValueError("PHASE_UNIT must be 'deg' or 'rad'")


def amp_phase_to_real(amp, phase):
    if REAL_EXPORT_MODE == "real_part":
        return amp * math.cos(phase_to_rad(phase))
    if REAL_EXPORT_MODE == "amp":
        return amp
    raise ValueError("REAL_EXPORT_MODE must be 'real_part' or 'amp'")


def read_modes(filename):
    """
    Format:
        mode freq damping node_id Ax Px Ay Py Az Pz
    """
    modes = {}

    with open(filename, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = clean_line(raw_line)
            if not line:
                continue

            parts = split_values(line)
            if len(parts) < 10:
                raise ValueError(f"Invalid mode row: {raw_line.strip()}")

            mode_no = int(parts[0])
            freq = float(parts[1])
            damping = float(parts[2])
            node_id = int(parts[3])

            ax = float(parts[4])
            px = float(parts[5])
            ay = float(parts[6])
            py = float(parts[7])
            az = float(parts[8])
            pz = float(parts[9])

            ux = amp_phase_to_real(ax, px)
            uy = amp_phase_to_real(ay, py)
            uz = amp_phase_to_real(az, pz)

            if mode_no not in modes:
                modes[mode_no] = {
                    "mode": mode_no,
                    "freq": freq,
                    "damping": damping,
                    "shape": {},
                }

            modes[mode_no]["shape"][node_id] = (ux, uy, uz)

    return [modes[key] for key in sorted(modes.keys())]


def check_data(nodes, lines, modes):
    node_ids = set(nodes.keys())

    for index, (n1, n2) in enumerate(lines, start=1):
        if n1 not in node_ids:
            raise ValueError(f"Element {index} references missing node {n1}")
        if n2 not in node_ids:
            raise ValueError(f"Element {index} references missing node {n2}")

    for mode in modes:
        mode_no = mode["mode"]
        shape_nodes = set(mode["shape"].keys())
        missing = node_ids - shape_nodes
        if missing:
            raise ValueError(f"Mode {mode_no} is missing node data: {sorted(missing)}")


def save_nodes_to_db(cursor, project_id, file_id, nodes):
    insert_sql = """
    INSERT INTO t_mt_py_test_node
    (nid, pid, fid, ics, ocs, x, y, z, origin_x, origin_y, origin_z)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    for node_id in sorted(nodes.keys()):
        x, y, z = nodes[node_id]
        cursor.execute(
            insert_sql,
            (
                str(node_id),
                int(project_id),
                int(file_id),
                0,
                0,
                float(x),
                float(y),
                float(z),
                float(x),
                float(y),
                float(z),
            ),
        )


def save_elements_to_db(cursor, project_id, lines):
    insert_sql = """
    INSERT INTO t_mt_py_test_element
    (element_no, pid, element_type, point1, point2, point3, point4)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """

    for element_no, (n1, n2) in enumerate(lines, start=1):
        cursor.execute(
            insert_sql,
            (
                int(element_no),
                int(project_id),
                "LINE2",
                int(n1),
                int(n2),
                None,
                None,
            ),
        )


def save_modes_to_db(cursor, project_id, file_id, modes):
    insert_freq_sql = """
    INSERT INTO t_mt_py_test_modal_frequency
    (mode_no, pid, fid, frequency, damping, eigenvalue_Re, eigenvalue_Im)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """

    insert_shape_sql = """
    INSERT INTO t_mt_py_test_modal_shape
    (mode_no, pid, modal_shape)
    VALUES (%s, %s, %s)
    """

    insert_real_sql = """
    INSERT INTO t_mt_py_test_modal_shape_real
    (mode_no, pid, point, ux, uy, uz)
    VALUES (%s, %s, %s, %s, %s, %s)
    """

    for mode in modes:
        mode_no = int(mode["mode"])
        freq = float(mode["freq"])
        damping = float(mode.get("damping", 0.0))
        shape = mode["shape"]

        cursor.execute(
            insert_freq_sql,
            (mode_no, int(project_id), int(file_id), freq, damping, None, None),
        )

        modal_shape_payload = {
            str(node_id): [float(values[0]), float(values[1]), float(values[2])]
            for node_id, values in sorted(shape.items())
        }
        cursor.execute(
            insert_shape_sql,
            (
                mode_no,
                int(project_id),
                json.dumps(modal_shape_payload, ensure_ascii=False),
            ),
        )

        for node_id in sorted(shape.keys()):
            ux, uy, uz = shape[node_id]
            cursor.execute(
                insert_real_sql,
                (
                    mode_no,
                    int(project_id),
                    int(node_id),
                    float(ux),
                    float(uy),
                    float(uz),
                ),
            )


def save_test_dimensions_to_db(cursor, project_id, nodes):
    points = [list(coords) for _node_id, coords in sorted(nodes.items())]
    save_test_model_dimensions(
        int(project_id),
        points=points,
        cursor=cursor,
    )


def import_txt_to_mysql(project_id, file_id=0, clear_before=True):
    ensure_tables_exist()

    nodes = read_nodes(NODES_TXT)
    lines = read_lines(LINES_TXT)
    modes = read_modes(MODES_TXT)
    check_data(nodes, lines, modes)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if clear_before:
            clear_unv_tables(cursor, int(project_id))

        save_nodes_to_db(cursor, project_id, file_id, nodes)
        save_elements_to_db(cursor, project_id, lines)
        save_modes_to_db(cursor, project_id, file_id, modes)
        save_test_dimensions_to_db(cursor, project_id, nodes)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    return {
        "project_id": int(project_id),
        "file_id": int(file_id),
        "node_count": len(nodes),
        "element_count": len(lines),
        "mode_count": len(modes),
    }


def main():
    result = import_txt_to_mysql(
        project_id=PROJECT_ID,
        file_id=FILE_ID,
        clear_before=CLEAR_BEFORE_INSERT,
    )

    print("TXT data has been written to MySQL")
    print("project_id:", result["project_id"])
    print("file_id:", result["file_id"])
    print("node_count:", result["node_count"])
    print("element_count:", result["element_count"])
    print("mode_count:", result["mode_count"])
    print("real_export_mode:", REAL_EXPORT_MODE)


if __name__ == "__main__":
    main()
