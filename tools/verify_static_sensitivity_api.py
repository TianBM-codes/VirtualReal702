"""Check real stored sensitivity through the same HTTP endpoints as the viewer."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.odb_client import decode_l3be


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--result-group", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = f"{args.base_url.rstrip('/')}/api/odb/{args.project_id}"
    evidence = {}

    def get_json(name, path, params=None):
        response = requests.get(base + path, params=params, timeout=60)
        response.raise_for_status()
        payload = response.json()
        assert payload["code"] == 200, payload
        evidence[name] = {"url": response.url, "status": response.status_code, "response": payload}
        return payload["data"]

    groups = get_json("result_groups", "/sensitivity/result_groups")
    assert args.result_group in groups["result_groups"], groups
    fields = get_json("fields", "/sensitivity/fields", {"result_group": args.result_group})
    assert fields["fields"], fields
    get_json("step_frame", "/sensitivity/step_frame")
    field = fields["fields"][0]
    params = {"result_group": args.result_group, "step": "Sensitivity",
              "field": field["field_name"], "frame": 0}
    ranges = get_json("frame_scalar_range", "/results/frame-scalar-range",
                      {**params, "instances": args.instance})
    assert args.instance in ranges["instance_ranges"], ranges
    response = requests.get(base + "/results/frame-scalars",
                            params={**params, "instance": args.instance}, timeout=60)
    response.raise_for_status()
    (output / "frame-scalars.l3be").write_bytes(response.content)
    sections = decode_l3be(response.content)
    values = sections["u_per_vertex"]
    legend = sections["legend_range"]
    assert values.size > 0 and np.isfinite(values).all(), "Empty or invalid scalars"
    assert np.isfinite(legend).all()
    assert np.allclose(legend, [ranges["global_min"], ranges["global_max"]], rtol=1e-6, atol=0)
    evidence["frame_scalars"] = {
        "url": response.url, "status": response.status_code, "bytes": len(response.content),
        "vertex_count": int(values.size), "finite_count": int(np.isfinite(values).sum()),
        "normalized_min": float(values.min()), "normalized_max": float(values.max()),
        "legend_range": legend.tolist(), "headers": dict(response.headers),
    }
    (output / "api-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
