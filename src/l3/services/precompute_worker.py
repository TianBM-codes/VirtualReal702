"""
全量预计算的并行 worker 进程（由 precompute_plan.run_plan_parallel 启动）。

    python -m src.l3.services.precompute_worker <odb_id> <workspace>

用独立命令启动而不是 multiprocessing：Windows 的 spawn 会在子进程里重新执行
主模块（内嵌 runner 时是 app.py），等于再起一份服务。

协议：stdin 每行一个 JSON 目标（precompute_plan.Target 的字段），stdout 每行一个
JSON 结果 {"ok", "stats", "entries", "rss_mb"} 或 {"ok": false, "error", "oom"}。stdout 的
原始描述符专供协议使用，库里的 print 一律改到 stderr，避免混进协议。
每个目标在严格模式下计算（只收集落盘条目，由父进程统一核验）。
"""
import json
import os
import sys


def _rss_mb():
    """本进程内存峰值（MB）：Windows 用 peak_wset，Linux/macOS 用 ru_maxrss，都没有才退回当前 rss。"""
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        peak = getattr(psutil.Process().memory_info(), "peak_wset", None)
        if peak:
            return round(peak / 2**20)
    try:
        import resource
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(kb / (2**20 if sys.platform == "darwin" else 2**10))
    except Exception:
        pass
    if psutil is None:
        return None
    return round(psutil.Process().memory_info().rss / 2**20)


def _is_memory_error(exc) -> bool:
    """异常链上有 MemoryError（numpy 的 _ArrayMemoryError 是其子类）即视为内存不足。"""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, MemoryError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def main() -> None:
    proto = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())   # C 库直接写 fd 1 也不会混进协议
    sys.stdout = sys.stderr
    odb_id, workspace = sys.argv[1], sys.argv[2]

    from ..core.state import ModelIndex, OdbRegistry
    from . import cache_store
    from .precompute_plan import target_from_json, run_plan

    idx = ModelIndex(odb_id, workspace)
    idx.load_l2_render_data()
    reg = OdbRegistry(max_loaded=1)
    reg.loaded[odb_id] = idx

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            target = target_from_json(json.loads(line))
            with cache_store.require_persistent_cache(verify=False) as entries:
                stats = run_plan(reg, odb_id, idx, [target], strict=True)
            out = {"ok": True, "stats": stats, "entries": sorted(entries), "rss_mb": _rss_mb()}
        except Exception as exc:
            out = {"ok": False, "error": str(exc), "oom": _is_memory_error(exc)}
        proto.write(json.dumps(out, ensure_ascii=False) + "\n")
        proto.flush()


if __name__ == "__main__":
    main()
