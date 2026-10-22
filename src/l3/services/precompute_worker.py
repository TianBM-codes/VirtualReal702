"""
全量预计算的并行 worker 进程（由 precompute_plan.run_plan_parallel 启动）。

    python -m src.l3.services.precompute_worker <odb_id> <workspace>

用独立命令启动而不是 multiprocessing：Windows 的 spawn 会在子进程里重新执行
主模块（内嵌 runner 时是 app.py），等于再起一份服务。

协议：stdin 每行一个 JSON 目标（precompute_plan.Target 的字段），stdout 每行一个
JSON 结果 {"ok", "stats", "entries", "rss_mb"} 或 {"ok": false, "error"}。stdout 的
原始描述符专供协议使用，库里的 print 一律改到 stderr，避免混进协议。
每个目标在严格模式下计算（只收集落盘条目，由父进程统一核验）。
"""
import json
import os
import sys


def _rss_mb():
    try:
        import psutil
    except ImportError:
        return None
    mi = psutil.Process().memory_info()
    return round((getattr(mi, "peak_wset", None) or mi.rss) / 2**20)


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
            out = {"ok": False, "error": str(exc)}
        proto.write(json.dumps(out, ensure_ascii=False) + "\n")
        proto.flush()


if __name__ == "__main__":
    main()
