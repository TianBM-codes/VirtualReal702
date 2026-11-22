"""Import the small nodal payload of a DSA run into an existing project."""
import json
import os
import tempfile
import time
from pathlib import Path

import h5py
import numpy as np

from ..infra.manifest_repo import ManifestRepo


def import_abaqus_sensitivity(*, odb_path, workspace, result_group, options, abaqus, run_command):
    started = time.monotonic()
    manifest = ManifestRepo(workspace)
    instances = [row["instance_name"] for row in manifest.list_instances()]
    if not instances:
        raise ValueError("Sensitivity extraction requires an existing project mesh")
    steps = options.get("steps") or []
    if len(steps) > 1:
        raise ValueError("Select one sensitivity step per extraction")
    frame = options.get("frames")
    if isinstance(frame, list):
        if len(frame) != 1:
            raise ValueError("Select one sensitivity frame per extraction")
        frame = frame[0]
    elif frame in (None, "last"):
        frame = None
    else:
        raise ValueError("Unsupported sensitivity frame selection")
    request = {
        "odb_path": os.path.abspath(odb_path),
        "step": steps[0] if steps else None,
        "frame": frame,
        "field_prefix": options["sensitivity_prefix"],
        "response_field": options["response_field"],
        "instances": instances,
    }
    script = Path(__file__).resolve().parents[3] / "tools" / "extract_abaqus_sensitivity.py"
    with tempfile.TemporaryDirectory(prefix="abaqus_sensitivity_") as temporary:
        request_path = Path(temporary) / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        code, tail = run_command([
            abaqus, "python", str(script), "--request", str(request_path), "--out", temporary,
        ])
        if code:
            raise RuntimeError("Sensitivity extraction failed: " + tail[-4000:])
        extracted = time.monotonic()
        metadata = json.loads((Path(temporary) / "metadata.json").read_text(encoding="utf-8"))
        frame_metadata = [{
            "frame_idx": 0, "frame_value": metadata["frame_value"],
            "description": metadata["description"],
        }]
        target = Path(workspace) / "l1" / "results" / result_group
        target.mkdir(parents=True, exist_ok=True)
        for field_index, field in enumerate(metadata["fields"]):
            output = target / f"sensitivity_{field_index:04d}.h5"
            with h5py.File(output, "w") as handle:
                handle.attrs["components"] = json.dumps(field["components"])
                handle.attrs["original_frame"] = metadata["original_frame"]
                for block in field["blocks"]:
                    instance = block["instance"]
                    labels = np.load(Path(temporary) / block["labels"], allow_pickle=False)
                    values = np.load(Path(temporary) / block["values"], allow_pickle=False)
                    group = handle.create_group(f"NODAL/{instance}")
                    group.create_dataset("labels", data=labels)
                    group.create_dataset("data", data=values[None], compression="gzip", compression_opts=1)
                    group.attrs["components"] = json.dumps(field["components"])
            relative = output.relative_to(workspace).as_posix()
            for block in field["blocks"]:
                manifest.register_external_result(
                    result_group=result_group, step_name=metadata["step"],
                    field_name=field["name"], file_path=relative,
                    components=field["components"], positions=["NODAL"],
                    instance_name=block["instance"], position="NODAL", frames=frame_metadata,
                )
                labels = np.load(Path(temporary) / block["labels"], allow_pickle=False)
                with manifest._get_conn() as connection:
                    connection.execute(
                        "UPDATE result_blocks SET h5_path=?, label_path=?, n_entities=? "
                        "WHERE result_group=? AND step_name=? AND field_name=? AND instance_name=?",
                        (f"/NODAL/{block['instance']}", f"/NODAL/{block['instance']}/labels", len(labels), result_group,
                         metadata["step"], field["name"], block["instance"]),
                    )
        with manifest._get_conn() as connection:
            connection.execute("UPDATE result_files SET source='odb' WHERE result_group=?", (result_group,))
            connection.execute(
                "UPDATE steps SET procedure=? WHERE result_group=? AND step_name=?",
                (metadata.get("procedure") or "STATIC", result_group, metadata["step"]),
            )
            connection.execute(
                "UPDATE result_group_meta SET source_file=? WHERE result_group=?",
                (os.path.basename(odb_path), result_group),
            )
        manifest.update_result_group_meta_display_name(
            result_group, options.get("display_name") or result_group)
    return {
        "field_count": len(metadata["fields"]), "step": metadata["step"],
        "original_frame": metadata["original_frame"], "stored_frame": 0,
        "extract_seconds": round(extracted - started, 3),
        "total_seconds": round(time.monotonic() - started, 3),
    }
