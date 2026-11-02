import json
from typing import Any

from fastapi import Request


async def log_request(request: Request, body: Any = None) -> None:
    # Do not copy model parameters or trial data into request logs.
    if request.method != "POST":
        return

    log_info = {
        "method": request.method,
        "path": request.url.path,
    }

    print("\n[Request Log]")
    print(json.dumps(log_info, indent=2, ensure_ascii=False, default=str))


def model_to_dict(model: Any) -> Any:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return model
