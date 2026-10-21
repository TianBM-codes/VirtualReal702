"""
L3 统一结果缓存（内存层 + 工作区磁盘层）。

设计见 docs/l3/L3-Precompute-Cache-Design.md。所有 L3 结果缓存（云图 scalar_vertex、
图例范围、顶点位移、变形/模态响应包、着色分类……）都经由本模块读写，统一容量、
统一开关、统一淘汰：

内存层（每个 worker 进程一份）：
  - 大值（ndarray / 嵌套 tuple）按总字节数 LRU，上限 APP_SCALAR_CACHE_MB（默认 512MB）；
  - 小值（范围、统计 dict）按条数 LRU。

磁盘层（每个工作区一份，重启不丢、多 worker 共享）：
  <workspace>/l3_cache/
    model/                  只依赖模型几何的条目（着色分类、单元边线……）
      arrays/<sha1>.npz
      json/<sha1>.json
    groups/<result_group>/  依赖结果文件的条目，按结果组分目录便于整组清理
      arrays/<sha1>.npz
      json/<sha1>.json
      payloads/<sha1>.bin   最终 L3BE 响应包（命中后原样返回）
  整个 l3_cache/ 合计不超过 APP_SCALAR_DISK_CACHE_MB（默认 100GB），超出按 mtime
  （命中时 touch）删最旧的文件；设为负数（如 -1）表示不限容量、不淘汰。容量扫描
  （遍历目录）在普通写入后最多每 30 秒一次，全量准备期间只在结束时做一次。写入一律"临时文件 + os.replace"原子写，读到损坏
  文件直接删除当未命中。删除项目时整个工作区被删，缓存随之清理。

开关：APP_SCALAR_CACHE_MB=0 关闭全部结果缓存（内存 + 磁盘）；
      APP_SCALAR_DISK_CACHE_MB=0 只关磁盘层，<0 不限容量。

缓存 key 由调用方构造，必须包含所依赖源文件的签名（file_sig / render_sig），
源文件被重写后 key 自然变化，旧条目由 LRU 淘汰。缓存中的数组是共享对象，
一律置为只读，下游只允许整体重新赋值。

这是 "L3 只写 manifest.db" 约定的唯一例外。
"""
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from collections import OrderedDict
from typing import Optional, Tuple

import numpy as np

from ..core.config import settings

logger = logging.getLogger(__name__)

MISS = object()          # 小值缓存未命中哨兵（缓存值本身可以是 None）
MODEL_BUCKET = "model"   # 只依赖模型几何的条目

_CACHE_DIR = "l3_cache"
# 统一缓存层之前的目录布局（scalars/ranges 为 perf 分支，其余为 9c2afe2 的整包缓存），
# 新代码不再读写，首次访问工作区时一次性删除（数据都可重算）。
_LEGACY_DIRS = (
    "scalars", "ranges", "raw_displacements", "deformed_normals",
    "deformed_payloads", "modal_payloads",
)
_SMALL_CACHE_MAX = 4096
_required_entries = ContextVar("required_cache_entries", default=None)


@contextmanager
def require_persistent_cache(*, verify: bool = True):
    """Runner 全量缓存准备：必须落盘，写失败/容量不足不能伪装成预热成功。

    yield 本轮需要的条目集合 {(workspace, path)}。verify=False 只收集不核验
    （并行 worker 用），由父进程把各 worker 的条目并入自己的集合后统一淘汰与核验。
    """
    if not disk_enabled():
        raise RuntimeError("全量缓存需要 APP_SCALAR_CACHE_MB 和 APP_SCALAR_DISK_CACHE_MB 均大于 0"
                           "（磁盘层也可设 -1 表示不限容量）")
    entries = set()
    token = _required_entries.set(entries)
    try:
        yield entries
        if not verify:
            return
        evict_workspace = {workspace for workspace, _path in entries}
        for workspace in evict_workspace:
            evict(workspace)
        missing = [path for _workspace, path in entries if not os.path.isfile(path)]
        if missing:
            raise RuntimeError("全量缓存未完整保留，请检查磁盘空间、权限和 APP_SCALAR_DISK_CACHE_MB：" + missing[0])
    finally:
        _required_entries.reset(token)


def _require_entry(workspace, path):
    entries = _required_entries.get()
    if entries is not None:
        entries.add((workspace, path))


# ─── 开关与签名 ───────────────────────────────────────────────────────────────

def enabled() -> bool:
    return settings.scalar_cache_mb > 0


def disk_enabled() -> bool:
    return settings.scalar_cache_mb > 0 and settings.scalar_disk_cache_mb != 0


def disk_unlimited() -> bool:
    return settings.scalar_disk_cache_mb < 0


def _disk_cap_bytes() -> int:
    if disk_unlimited():
        return sys.maxsize
    return settings.scalar_disk_cache_mb * 1024 * 1024


def file_sig(path: Optional[str]) -> Optional[Tuple[int, int]]:
    """源文件的 (mtime_ns, size) 签名；文件不可 stat 时返回 None（调用方不缓存）。"""
    if not path:
        return None
    try:
        st = os.stat(path)
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def render_sig(workspace: str, instance: str) -> Optional[Tuple[int, int]]:
    """instance 渲染几何（L2 render.h5）的签名：L2 重跑后依赖几何的条目自然失效。"""
    return file_sig(os.path.join(workspace, "l2", "render", f"{instance}_render.h5"))


_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]")


def group_bucket(result_group: Optional[str]) -> str:
    """结果组 → 磁盘子目录名。无结果组（legacy ODB job）归入 _default。"""
    if result_group is None or str(result_group) == "":
        return "groups/_default"
    name = str(result_group)
    safe = _SAFE_RE.sub("_", name)
    if safe != name or safe in (".", ".."):
        # 有非法字符时追加短哈希，避免 "a/b" 与 "a_b" 撞目录
        safe = f"{safe}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}"
    return f"groups/{safe}"


# ─── 内存层 ───────────────────────────────────────────────────────────────────

_LOCK = threading.Lock()
_BIG: "OrderedDict[tuple, object]" = OrderedDict()
_BIG_BYTES = 0
_SMALL: "OrderedDict[tuple, object]" = OrderedDict()


def _nbytes(value) -> int:
    """值中全部 ndarray / bytes 的总字节数（值可以是数组或嵌套 tuple/list）。"""
    if isinstance(value, np.ndarray):
        return int(value.nbytes)
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, (tuple, list)):
        return sum(_nbytes(v) for v in value)
    return 0


def mem_get(key: tuple):
    """大值内存层：命中返回值，未命中返回 None。"""
    if _required_entries.get() is not None:
        return None  # runner 的内存不能供另一个 Web 进程复用，必须核查磁盘层
    with _LOCK:
        item = _BIG.get(key)
        if item is not None:
            _BIG.move_to_end(key)
        return item


def mem_put(key: tuple, value) -> None:
    global _BIG_BYTES
    cap = settings.scalar_cache_mb * 1024 * 1024
    nbytes = _nbytes(value)
    if cap <= 0 or nbytes > cap:
        return
    with _LOCK:
        old = _BIG.pop(key, None)
        if old is not None:
            _BIG_BYTES -= _nbytes(old)
        _BIG[key] = value
        _BIG_BYTES += nbytes
        while _BIG_BYTES > cap and _BIG:
            _, evicted = _BIG.popitem(last=False)
            _BIG_BYTES -= _nbytes(evicted)


def small_get(key: tuple, *, intermediate: bool = False):
    """小值内存层：命中返回值（可能是 None），未命中返回 MISS。

    严格准备期间默认不命中（最终条目必须核查磁盘层）；intermediate=True 的
    中间结果（例如被 frame-scalars smooth/flat 与 frame-scalar-range 共用的全模型
    平均范围）只用于本进程内少算几遍，不代表任何条目已落盘，允许命中。
    """
    if _required_entries.get() is not None and not intermediate:
        return MISS
    with _LOCK:
        if key in _SMALL:
            _SMALL.move_to_end(key)
            return _SMALL[key]
        return MISS


def small_put(key: tuple, value) -> None:
    if not enabled():
        return
    with _LOCK:
        _SMALL[key] = value
        _SMALL.move_to_end(key)
        while len(_SMALL) > _SMALL_CACHE_MAX:
            _SMALL.popitem(last=False)


def clear_memory() -> None:
    """清空本进程内存层（测试与运维用）。磁盘层不动。"""
    global _BIG_BYTES
    with _LOCK:
        _BIG.clear()
        _SMALL.clear()
        _BIG_BYTES = 0


# ─── 磁盘层 ───────────────────────────────────────────────────────────────────

_purged_workspaces: set = set()


def cache_root(workspace: str) -> str:
    return os.path.join(workspace, _CACHE_DIR)


def _purge_legacy_once(root: str) -> None:
    if root in _purged_workspaces:
        return
    _purged_workspaces.add(root)
    for name in _LEGACY_DIRS:
        p = os.path.join(root, name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            logger.info("l3 cache: removed legacy cache dir %s", p)


def _entry_path(workspace: str, bucket: str, kind: str, key: tuple, ext: str) -> str:
    root = cache_root(workspace)
    _purge_legacy_once(root)
    name = hashlib.sha1(repr(key).encode("utf-8")).hexdigest() + ext
    return os.path.join(root, *bucket.split("/"), kind, name)


def _touch(path: str) -> None:
    try:
        os.utime(path)   # LRU 触碰
    except OSError:
        pass


def _drop(path: str) -> None:
    logger.warning("l3 cache: dropping unreadable entry %s", path)
    try:
        os.remove(path)
    except OSError:
        pass


def _atomic_write(path: str, write_fn) -> bool:
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "wb") as fh:
            write_fn(fh)
        os.replace(tmp, path)
        return True
    except OSError:
        logger.warning("l3 cache: write failed for %s", path, exc_info=True)
        try:
            os.remove(tmp)
        except OSError:
            pass
        if _required_entries.get() is not None:
            raise
        return False


_EVICT_INTERVAL_S = 30.0
_last_evict: dict = {}


def _evict_after_write(workspace: str) -> None:
    """写入后的容量检查：全量准备期间推迟到结束时统一做（require_persistent_cache），
    平时每个工作区最多 30 秒扫一次目录——条目上万时每写一次就遍历一遍代价是平方级。"""
    if _required_entries.get() is not None or disk_unlimited():
        return
    now = time.monotonic()
    if now - _last_evict.get(workspace, -_EVICT_INTERVAL_S) < _EVICT_INTERVAL_S:
        return
    _last_evict[workspace] = now
    evict(workspace)


def evict(workspace: str, cap_bytes: Optional[int] = None) -> None:
    """整个 l3_cache/ 超过容量时按 mtime 从旧到新删除文件。"""
    if cap_bytes is None and disk_unlimited():
        return
    cap = _disk_cap_bytes() if cap_bytes is None else cap_bytes
    root = cache_root(workspace)
    entries = []
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn.startswith("."):
                continue        # .warmup.lock / .warmup_done 等控制文件不参与淘汰
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            entries.append((st.st_mtime_ns, st.st_size, p))
            total += st.st_size
    if total <= cap:
        return
    entries.sort()
    for _, size, p in entries:
        try:
            os.remove(p)
        except OSError:
            continue
        total -= size
        if total <= cap:
            break


def _fits(nbytes: int) -> bool:
    fits = disk_enabled() and nbytes <= _disk_cap_bytes()
    if not fits and _required_entries.get() is not None:
        raise RuntimeError("全量缓存单项超过磁盘缓存容量，请增大 APP_SCALAR_DISK_CACHE_MB")
    return fits


def arrays_get(workspace: str, bucket: str, key: tuple) -> Optional[dict]:
    """npz 条目 → {name: 只读数组}；未命中 / 损坏返回 None。"""
    if not disk_enabled():
        return None
    path = _entry_path(workspace, bucket, "arrays", key, ".npz")
    try:
        with np.load(path, allow_pickle=False) as z:
            arrays = {name: z[name] for name in z.files}
    except FileNotFoundError:
        return None
    except Exception:
        _drop(path)
        return None
    _touch(path)
    _require_entry(workspace, path)
    for arr in arrays.values():
        arr.setflags(write=False)
    return arrays


def arrays_put(workspace: str, bucket: str, key: tuple, arrays: dict) -> None:
    if not _fits(sum(int(np.asarray(a).nbytes) for a in arrays.values())):
        return
    path = _entry_path(workspace, bucket, "arrays", key, ".npz")
    _require_entry(workspace, path)
    if os.path.exists(path):
        return
    if _atomic_write(path, lambda fh: np.savez(fh, **arrays)):
        _evict_after_write(workspace)


def json_get(workspace: str, bucket: str, key: tuple):
    """小结果 JSON 条目：命中返回值（可能是 None），未命中返回 MISS。"""
    if not disk_enabled():
        return MISS
    path = _entry_path(workspace, bucket, "json", key, ".json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            value = json.load(fh)["v"]
    except FileNotFoundError:
        return MISS
    except Exception:
        _drop(path)
        return MISS
    _touch(path)
    _require_entry(workspace, path)
    return value


def json_put(workspace: str, bucket: str, key: tuple, value) -> None:
    """value 必须可 JSON 序列化。小文件不触发淘汰扫描（由大条目写入时一并淘汰）。"""
    if not disk_enabled():
        return
    path = _entry_path(workspace, bucket, "json", key, ".json")
    _require_entry(workspace, path)
    if os.path.exists(path):
        return
    data = json.dumps({"v": value}, ensure_ascii=False).encode("utf-8")
    _atomic_write(path, lambda fh: fh.write(data))


def payload_get(workspace: str, bucket: str, key: tuple) -> Optional[bytes]:
    """最终响应包（bytes）；未命中返回 None。"""
    if not disk_enabled():
        return None
    path = _entry_path(workspace, bucket, "payloads", key, ".bin")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return None
    except OSError:
        _drop(path)
        return None
    _touch(path)
    _require_entry(workspace, path)
    return data


def payload_put(workspace: str, bucket: str, key: tuple, payload: bytes) -> None:
    if not _fits(len(payload)):
        return
    path = _entry_path(workspace, bucket, "payloads", key, ".bin")
    _require_entry(workspace, path)
    if os.path.exists(path):
        return
    if _atomic_write(path, lambda fh: fh.write(payload)):
        _evict_after_write(workspace)


def clear_bucket(workspace: str, bucket: str) -> None:
    """删除一个桶（例如某结果组）的全部磁盘条目。内存层靠 key 中的文件签名自然失效。"""
    shutil.rmtree(os.path.join(cache_root(workspace), *bucket.split("/")),
                  ignore_errors=True)


def clear_workspace(workspace: str) -> None:
    """删除整个工作区的磁盘缓存（L2 重跑等几何变化时用）。"""
    shutil.rmtree(cache_root(workspace), ignore_errors=True)
