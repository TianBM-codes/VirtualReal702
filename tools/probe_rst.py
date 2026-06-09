# -*- coding: utf-8 -*-
"""
probe_rst.py — 诊断一个 RST 文件为什么 ansys-mapdl-reader 读不动。

用法：
    python tools/probe_rst.py E:\\path\\to\\file.rst

它会临时把崩溃点（_load_element_table 里 max(空字典)）改成容错版，
然后尽量把 RST 头信息、单元/节点数、可用结果、网格规模打出来，
据此判断：文件是被截断、版本不认、还是本身就不含网格。
"""
import os
import sys
import traceback


def main():
    if len(sys.argv) < 2:
        print("用法: python tools/probe_rst.py <file.rst>")
        return 2

    path = sys.argv[1]
    print("=" * 60)
    print("file      :", path)
    print("exists    :", os.path.isfile(path))
    if os.path.isfile(path):
        print("size      : %d bytes (%.2f MB)" % (
            os.path.getsize(path), os.path.getsize(path) / 1024.0 / 1024.0))
    print("=" * 60)

    try:
        from ansys.mapdl import reader as pymapdl_reader
        from ansys.mapdl.reader import rst as _rst
    except Exception as e:
        print("import ansys-mapdl-reader 失败:", e)
        return 1

    try:
        print("reader version:", getattr(pymapdl_reader, "__version__", "?"))
    except Exception:
        pass

    # 把会崩的 _load_element_table 包成容错版：失败就把表置空，让 __init__ 继续，
    # 这样我们能拿到 _resultheader 等信息来诊断。
    orig_let = _rst.Result._load_element_table

    def safe_let(self):
        try:
            return orig_let(self)
        except Exception as e:
            print("[warn] _load_element_table 失败 -> 置空继续:", repr(e))
            self._element_table = None
    _rst.Result._load_element_table = safe_let

    # 1) 读头信息（read_mesh=False，最少依赖）
    rst = None
    try:
        rst = _rst.Result(path, read_mesh=False)
        print("\n[1] Result(read_mesh=False) 构造成功")
    except Exception:
        print("\n[1] Result(read_mesh=False) 仍然失败：")
        traceback.print_exc()
        return 1

    # 2) 打印结果头（含 nelm / nnod / 版本等）
    print("\n[2] _resultheader 关键字段：")
    h = getattr(rst, "_resultheader", {}) or {}
    for k in ("mainver", "subver", "verstring", "nnod", "nelm",
              "numdof", "neqv", "maxn", "nsets", "ptrGEO", "kan"):
        if k in h:
            print("    %-10s = %s" % (k, h.get(k)))
    extra = [k for k in h.keys() if k not in {
        "mainver", "subver", "verstring", "nnod", "nelm",
        "numdof", "neqv", "maxn", "nsets", "ptrGEO", "kan"}]
    if extra:
        print("    其它字段:", sorted(extra))

    # 3) 单元类型表是否真的空
    print("\n[3] 单元类型表 element_type：")
    try:
        et = getattr(rst, "_element_type", None)
        print("    _element_type =", et)
    except Exception as e:
        print("    读取失败:", e)

    # 4) 尝试拿网格（这一步若 element_table 为空通常会失败）
    print("\n[4] 尝试读取网格 rst.grid：")
    try:
        rst2 = pymapdl_reader.read_binary(path)  # 完整读，可能再崩
        grid = rst2.grid
        print("    n_points =", grid.n_points)
        print("    n_cells  =", grid.n_cells)
        try:
            print("    celltypes(unique) =", sorted(set(grid.celltypes.tolist())))
        except Exception:
            pass
    except Exception:
        print("    读取网格失败（很可能正是元素表为空导致）：")
        traceback.print_exc()

    # 5) 可用结果集 / 材料
    print("\n[5] 结果与材料：")
    try:
        print("    n_results =", rst.n_results)
    except Exception as e:
        print("    n_results 读取失败:", e)
    try:
        mats = getattr(rst, "materials", None)
        print("    materials keys =", list(mats.keys()) if mats else mats)
    except Exception as e:
        print("    materials 读取失败:", e)

    print("\n" + "=" * 60)
    print("诊断要点：")
    print("  - 若 nelm=0 / nnod=0 -> 文件本身不含网格（子结构/重启/截断）")
    print("  - 若 nelm>0 但 _element_type 为空 -> reader 没认出单元表（版本/格式问题）")
    print("  - 若 [4] 能拿到 n_cells>0 -> 我们可以加容错补丁直接读")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
