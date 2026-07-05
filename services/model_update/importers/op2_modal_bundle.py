import json
from pathlib import Path

import numpy as np

from services.model_update.importers.op2_service import build_modal_import_payload


OP2_PATH = r"D:\your\model.op2"
BDF_PATH = r"D:\your\model.bdf"
OUTPUT_JSON = r"D:\temp\op2_modal_bundle.json"
MODE_NUMBERS = None  # e.g. [1, 2, 3]
SUBCASE_ID = None


def _default(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    raise TypeError(type(x).__name__)


if __name__ == "__main__":
    payload = build_modal_import_payload(
        op2_path=str(Path(OP2_PATH).expanduser().resolve()),
        bdf_path=str(Path(BDF_PATH).expanduser().resolve()) if str(BDF_PATH).strip() else None,
        subcase_id=SUBCASE_ID,
        mode_numbers=MODE_NUMBERS,
    )
    out_path = Path(OUTPUT_JSON).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_default), encoding="utf-8")

    print("json:", out_path)
    print("mode_count:", len(payload.get("modes") or []))
    print("warning_count:", len(payload.get("warnings") or []))
