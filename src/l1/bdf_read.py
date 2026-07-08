#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
bdf_read.py — 健壮的 BDF 读取封装，解决 pyNastran 在中文 Windows 上的编码崩溃。

背景
----
pyNastran 的 ``BDF.read_bdf()`` 在真正解析前，会先调用内部的
``_parse_primary_file_header()`` 扫描文件头里的 ``$ pyNastran: ...`` 提示行。
这个函数用的是 **不带 encoding 参数的** ``open(path, 'r')``：

    with open(bdf_filename, 'r') as bdf_file:   # 用系统 locale 编码

在中文 Windows 上 ``open()`` 的默认编码是 **gbk(cp936)**。如果 BDF 文件本身
是 UTF-8（含中文注释、°/µ 等符号，或带 UTF-8 BOM），gbk 解不出来 → 触发
UnicodeDecodeError；pyNastran 转入 ``errors='replace'`` 分支，把非法字节替换成
替换字符 ``\\ufffd``，再把这些行打到日志。日志往 gbk 控制台一写，``\\ufffd``
又编码不回 gbk，于是抛出用户看到的：

    UnicodeEncodeError: 'gbk' codec can't encode character '\\ufffd' ...
        model.read_bdf  ...  _parse_primary_file_header

关键点：**给 read_bdf 传 encoding= 参数也没用**，因为头部扫描那一步压根不看
encoding，永远用 locale 默认编码 open。所以必须在我们这一层兜住。

策略
----
1. 先探测文件真实编码（utf-8-sig / utf-8 / gbk / latin-1）。
2. 正常路径：带上探测到的 encoding 调 read_bdf。
   - 在 Linux/mac（默认 utf-8）上直接成功。
   - 在中文 Windows 上，只要文件能被 gbk 解开也直接成功。
3. 兜底路径：只有当头部扫描真的因 locale 编码崩溃（UnicodeError）时，才在
   源文件同目录写一个「纯 ASCII 化」的临时副本再读。非 ASCII 字符只会出现在
   ``$`` 注释里（Nastran 卡片数据是纯数字/ASCII），把它们替换成 '?' 对几何提取
   毫无影响；放在同目录是为了让 ``INCLUDE`` 相对路径仍能解析。

对外只暴露 ``read_bdf_safe()``，所有 read_bdf 调用点都应改走它。
"""

import os
import sys

# 探测顺序：BOM 版 UTF-8 → 纯 UTF-8 → gbk（中文 Windows 常见）→ latin-1（永远成功兜底）
_CANDIDATE_ENCODINGS = ('utf-8-sig', 'utf-8', 'gbk', 'latin-1')


def detect_bdf_encoding(path):
    """探测 BDF 文件的真实文本编码，返回能完整解码它的第一个候选编码名。"""
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in _CANDIDATE_ENCODINGS:
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    # latin-1 理论上不会失败，兜底返回它
    return 'latin-1'


def _write_ascii_sidecar(path, encoding):
    """在源文件同目录写一个纯 ASCII 化的临时副本，返回其路径。

    非 ASCII 字符（几乎只出现在 $ 注释里）用 '?' 替换。同目录放置保证
    INCLUDE 的相对路径仍然可解析。
    """
    with open(path, 'rb') as f:
        text = f.read().decode(encoding, errors='replace')
    ascii_text = text.encode('ascii', errors='replace').decode('ascii')

    d, base = os.path.split(os.path.abspath(path))
    root, ext = os.path.splitext(base)
    sidecar = os.path.join(d, root + '.pyn_ascii' + (ext or '.bdf'))
    with open(sidecar, 'w', encoding='ascii', newline='') as f:
        f.write(ascii_text)
    return sidecar


def read_bdf_safe(bdf_filename, xref=True, punch=False, debug=False,
                  model=None, **read_kwargs):
    """构造并读取一个 pyNastran BDF，规避中文 Windows 的 locale 编码崩溃。

    参数
    ----
    bdf_filename : str
        .bdf/.dat/.nas 路径。
    xref, punch : 透传给 read_bdf。
    debug : 透传给 BDF() 构造函数（默认 False，安静）。
    model : 可选，已构造好的空 BDF 实例；不传则内部新建。
        注意兜底重试时需要一个「干净」的 model，因此更推荐不传、由本函数管理。
    **read_kwargs : 其余透传给 read_bdf（如 read_includes）。

    返回
    ----
    读取完成的 BDF 实例。
    """
    from pyNastran.bdf.bdf import BDF

    enc = detect_bdf_encoding(bdf_filename)

    def _new_model():
        return model if model is not None else BDF(debug=debug)

    m = _new_model()
    try:
        m.read_bdf(bdf_filename, xref=xref, punch=punch, encoding=enc, **read_kwargs)
        return m
    except UnicodeError:
        # 头部扫描用 locale 编码 open 崩了（典型：中文 Windows + UTF-8 文件）。
        # 用纯 ASCII 化的同目录副本重读。
        sidecar = _write_ascii_sidecar(bdf_filename, enc)
        sys.stderr.write(
            "read_bdf_safe: 源文件编码({})与系统 locale 冲突，"
            "已改用 ASCII 化副本读取: {}\n".format(enc, sidecar))
        # 若外部传入了 model，它此刻可能已被半初始化污染，这里只能新建一个干净的
        m2 = BDF(debug=debug)
        try:
            m2.read_bdf(sidecar, xref=xref, punch=punch, encoding='utf-8', **read_kwargs)
            return m2
        finally:
            try:
                os.remove(sidecar)
            except OSError:
                pass
