"""HTTP access to both documented SimRight deployments. Requires requests."""

import math
from typing import Any, Dict, Optional

import requests


class SimRightError(RuntimeError):
    """HTTP, protocol, or vendor business error."""


class SimRightClient:
    PROFILES = {
        "markdown": ("/applications/3dlite/api/v1/query", "filename"),
        "excel": ("/applications/3dlite/api/v1/query/query", "fileName"),
    }

    def __init__(self, base_url: str, *, profile: str = "markdown",
                 query_path: Optional[str] = None, filename_key: Optional[str] = None,
                 timeout: float = 60, headers: Optional[Dict[str, str]] = None,
                 success_codes=(0,), session=None):
        if profile not in self.PROFILES:
            raise ValueError("profile must be markdown or excel")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        path, key = self.PROFILES[profile]
        self.base_url = base_url.rstrip("/")
        self.query_path = query_path or path
        self.filename_key = filename_key or key
        if not self.query_path.startswith("/"):
            raise ValueError("query_path must start with /")
        if self.filename_key not in ("filename", "fileName"):
            raise ValueError("filename_key must be filename or fileName")
        self.timeout = timeout
        self.success_codes = tuple(success_codes)
        self.session = session if session is not None else requests.Session()
        self.headers = dict(headers or {})

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _request(self, method, path, **kwargs):
        try:
            response = self.session.request(
                method, self.base_url + path, timeout=self.timeout,
                headers=self.headers, **kwargs,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SimRightError("SimRight HTTP/JSON request failed: %s" % exc) from exc

    def query(self, name: str, filename: Optional[str] = None, **args) -> Any:
        """Return data, validating the vendor envelope (success code defaults to 0).

        No retry/fallback: rendering, probe creation and deletion may have side effects.
        All documented dispatch names and deployment extensions can be passed here.
        """
        if not name:
            raise ValueError("name is required")
        args = dict(args)
        alternate = args.pop("fileName", None)
        if filename is not None and alternate is not None and filename != alternate:
            raise ValueError("filename and fileName disagree")
        filename = filename if filename is not None else alternate
        if filename is not None:
            if not filename:
                raise ValueError("filename cannot be empty")
            args[self.filename_key] = filename
        if name != "modelDiff" and not filename:
            raise ValueError("filename is required")
        body = self._request("POST", self.query_path, json={"name": name, "args": args})
        if not isinstance(body, dict) or "code" not in body:
            raise SimRightError("Response must contain code/data/message")
        if isinstance(body["code"], bool) or body["code"] not in self.success_codes:
            raise SimRightError("%s failed (code=%r): %s" %
                                (name, body["code"], body.get("message", "")))
        if "data" not in body:
            raise SimRightError("Successful response is missing data")
        return body["data"]

    def loadcases(self, filename):
        return self.query("loadcases", filename)

    def variables(self, filename, **options):
        return self.query("variables", filename, **options)

    def assemble(self, filename):
        return self.query("assemble", filename)

    def node_ids(self, filename, positions, **options):
        return self.query("nodeId", filename, nodePosList=positions, **options)

    def node_info(self, filename, node_ids, var_name, **options):
        return self.query("nodeInfo", filename, nodeIds=list(node_ids),
                          varName=var_name, **options)

    def element_info(self, filename, element_ids, var_name, **options):
        return self.query("elementInfo", filename, elemIds=list(element_ids),
                          varName=var_name, **options)

    def xy_curves(self, filename, node_ids, y_var_name, **options):
        return self.query("XYCurveData1", filename, nodeIds=list(node_ids),
                          yVarName=y_var_name, **options)

    def probe_group(self, filename, positions, **options):
        return self.query("probeGroupByPos", filename, nodePosList=positions, **options)

    def submit_conversion(self, model_path, uuid, *, method="POST", path="/api/task/submit"):
        """Excel specifies payload only; method is an explicit, unverified convention.

        Return the raw JSON: no task status/response contract was supplied.
        """
        if method.upper() not in ("GET", "POST"):
            raise ValueError("method must be GET or POST")
        payload = {"modelPath": model_path, "uuid": uuid}
        return self._request(method.upper(), path,
                             **({"params": payload} if method.upper() == "GET"
                                else {"json": payload}))

    def conversion_status(self, uuid, *, method="GET", path="/api/task/query"):
        """Return raw JSON; GET is assumed and configurable, no automatic polling."""
        if method.upper() not in ("GET", "POST"):
            raise ValueError("method must be GET or POST")
        return self._request(method.upper(), path,
                             **({"params": {"uuid": uuid}} if method.upper() == "GET"
                                else {"json": {"uuid": uuid}}))

    def create_session(self, scenes, view_layout=1, *, method="POST"):
        """Additional multi-window API; POST is assumed, not specified in the source.

        Its documented success code is 1, distinct from DATA-API code 0. Return raw JSON.
        """
        if method.upper() not in ("GET", "POST"):
            raise ValueError("method must be GET or POST")
        payload = {"viewLayout": view_layout, "scenes": scenes}
        return self._request(method.upper(), "/applications/3dlite/create_session",
                             **({"params": payload} if method.upper() == "GET"
                                else {"json": payload}))
