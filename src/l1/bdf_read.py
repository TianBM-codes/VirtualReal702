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
import re
import sys

# 探测顺序：BOM 版 UTF-8 → 纯 UTF-8 → gbk（中文 Windows 常见）→ latin-1（永远成功兜底）
_CANDIDATE_ENCODINGS = ('utf-8-sig', 'utf-8', 'gbk', 'latin-1')
_PROD_FIELD_MISSING_A_RE = re.compile(r"\bA\s*=\s*None\b.*\bfield\s*#?3\b", re.IGNORECASE | re.DOTALL)

_PROPERTY_TO_ELEMENTS = {
    'PSHELL': {'CQUAD4', 'CQUADR', 'CQUAD8', 'CTRIA3', 'CTRIAR', 'CTRIA6'},
    'PCOMP': {'CQUAD4', 'CQUADR', 'CQUAD8', 'CTRIA3', 'CTRIAR', 'CTRIA6'},
    'PCOMPG': {'CQUAD4', 'CQUADR', 'CQUAD8', 'CTRIA3', 'CTRIAR', 'CTRIA6'},
    'PSHEAR': {'CSHEAR'},
    'PBAR': {'CBAR'},
    'PBEAM': {'CBEAM'},
    'PBEND': {'CBEND'},
    'PBARL': {'CBAR'},
    'PBEAML': {'CBEAM'},
    'PROD': {'CROD'},
    'PTUBE': {'CTUBE'},
    'PBUSH': {'CBUSH'},
    'PBUSH1D': {'CBUSH1D'},
    'PELAS': {'CELAS1'},
    'PDAMP': {'CDAMP1'},
    'PMASS': {'CMASS1'},
    'PSOLID': {'CHEXA', 'CPENTA', 'CTETRA'},
}
_PROPERTY_CARDS = set(_PROPERTY_TO_ELEMENTS)


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


def _split_bdf_fields(line):
    """Split a single-line BDF card into fields for simple free/fixed-format scans."""
    text = line.split('$', 1)[0].rstrip('\r\n')
    if not text.strip():
        return []
    if ',' in text:
        return [field.strip() for field in text.split(',')]
    return text.split()


def _sort_bdf_id_key(value):
    return int(value) if str(value).isdigit() else str(value)


def _line_newline(line):
    stripped = line.rstrip('\r\n')
    return line[len(stripped):] or '\n'


def _format_free_card(fields, comment=''):
    body = ','.join(str(field) for field in fields)
    if comment:
        body += ' $' + comment.strip()
    return body


def _is_duplicate_ids_error(exc):
    return exc.__class__.__name__ == 'DuplicateIDsError'


def _scan_duplicate_property_cleanup(path, encoding):
    """Renumber duplicate property IDs by property card type and matching element type."""
    with open(path, 'rb') as f:
        text = f.read().decode(encoding, errors='replace')

    raw_lines = text.splitlines(keepends=True)
    parsed = []
    property_entries = []
    used_pids = set()

    for lineno, line in enumerate(raw_lines, start=1):
        base, _, comment = line.partition('$')
        fields = _split_bdf_fields(line)
        card = fields[0].upper().rstrip('*') if fields else ''
        parsed.append({
            'lineno': lineno,
            'line': line,
            'fields': fields,
            'card': card,
            'comment': comment.rstrip('\r\n'),
        })
        if card not in _PROPERTY_CARDS or len(fields) < 2:
            continue
        pid = fields[1]
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        used_pids.add(pid_int)
        property_entries.append({
            'lineno': lineno,
            'card': card,
            'pid': pid_int,
            'fields': fields,
            'comment': comment.rstrip('\r\n'),
        })

    if not property_entries:
        return None

    next_pid = max(used_pids) + 1
    seen_types_by_pid = {}
    renumber_by_card_pid = {}
    renumber_by_line = {}
    duplicate_pids = set()
    duplicate_types = []
    skip_property_lines = set()

    for entry in property_entries:
        pid = entry['pid']
        card = entry['card']
        seen_types = seen_types_by_pid.setdefault(pid, set())
        if not seen_types:
            seen_types.add(card)
            continue

        duplicate_pids.add(pid)
        if card in seen_types:
            # Same property card type + same PID is genuinely ambiguous for elements.
            # Keep the first definition so pyNastran can continue parsing.
            skip_property_lines.add(entry['lineno'])
            continue

        while next_pid in used_pids:
            next_pid += 1
        new_pid = next_pid
        next_pid += 1
        used_pids.add(new_pid)
        renumber_by_card_pid[(card, pid)] = new_pid
        renumber_by_line[entry['lineno']] = new_pid
        duplicate_types.append('{}:{}->{}'.format(card, pid, new_pid))
        seen_types.add(card)

    if not renumber_by_card_pid and not skip_property_lines:
        return None

    rewritten = {}
    for item in parsed:
        lineno = item['lineno']
        fields = item['fields']
        card = item['card']
        if not fields:
            continue
        newline = _line_newline(item['line'])

        if lineno in skip_property_lines:
            stripped = item['line'].rstrip('\r\n')
            rewritten[lineno] = '$ read_bdf_safe skipped duplicate property: ' + stripped + newline
            continue

        if card in _PROPERTY_CARDS and len(fields) >= 2:
            try:
                old_pid = int(fields[1])
            except (TypeError, ValueError):
                old_pid = None
            if lineno in renumber_by_line:
                new_fields = list(fields)
                new_fields[1] = str(renumber_by_line[lineno])
                rewritten[lineno] = _format_free_card(new_fields, item['comment']) + newline
                continue

        if len(fields) >= 3:
            try:
                elem_pid = int(fields[2])
            except (TypeError, ValueError):
                elem_pid = None
            if elem_pid is not None:
                for (prop_card, old_pid), new_pid in renumber_by_card_pid.items():
                    if elem_pid == old_pid and card in _PROPERTY_TO_ELEMENTS.get(prop_card, ()):
                        new_fields = list(fields)
                        new_fields[2] = str(new_pid)
                        rewritten[lineno] = _format_free_card(new_fields, item['comment']) + newline
                        break

    if not rewritten:
        return None

    return {
        'text': text,
        'raw_lines': raw_lines,
        'rewritten': rewritten,
        'duplicate_pids': sorted(duplicate_pids),
        'renumbered': duplicate_types,
        'skipped_same_type_count': len(skip_property_lines),
    }


def _scan_invalid_prod_cleanup(path, encoding):
    """Find PROD cards missing required A and dependent CROD cards."""
    invalid_prod_pids = set()
    invalid_prod_lines = []
    dependent_crod_eids = set()
    dependent_crod_lines = []
    line_entries = []

    with open(path, 'rb') as f:
        text = f.read().decode(encoding, errors='replace')

    for lineno, line in enumerate(text.splitlines(keepends=True), start=1):
        fields = _split_bdf_fields(line)
        line_entries.append((line, fields))
        if not fields:
            continue

        card_name = fields[0].upper()
        if card_name == 'PROD':
            if len(fields) >= 2:
                pid = fields[1]
            else:
                pid = None
            a_field = fields[3] if len(fields) >= 4 else ''
            if pid and not a_field:
                invalid_prod_pids.add(pid)
                invalid_prod_lines.append(lineno)

    if not invalid_prod_pids:
        return None

    for lineno, (_, fields) in enumerate(line_entries, start=1):
        if not fields:
            continue
        if fields[0].upper() != 'CROD':
            continue
        if len(fields) < 3:
            continue
        pid = fields[2]
        if pid in invalid_prod_pids:
            eid = fields[1] if len(fields) >= 2 else None
            if eid:
                dependent_crod_eids.add(eid)
            dependent_crod_lines.append(lineno)

    return {
        'invalid_prod_pids': sorted(invalid_prod_pids, key=lambda value: int(value) if str(value).isdigit() else str(value)),
        'invalid_prod_lines': invalid_prod_lines,
        'dependent_crod_eids': sorted(dependent_crod_eids, key=lambda value: int(value) if str(value).isdigit() else str(value)),
        'dependent_crod_lines': dependent_crod_lines,
        'text': text,
    }


def _write_cleanup_sidecar(path, encoding, cleanup):
    """Write a temporary BDF that comments invalid PROD/CROD cards for visualization fallback."""
    lines_to_skip = set(cleanup['invalid_prod_lines']) | set(cleanup['dependent_crod_lines'])
    raw_lines = cleanup['text'].splitlines(keepends=True)

    d, base = os.path.split(os.path.abspath(path))
    root, ext = os.path.splitext(base)
    sidecar = os.path.join(d, root + '.pyn_clean' + (ext or '.bdf'))

    with open(sidecar, 'w', encoding=encoding, newline='') as f:
        for lineno, line in enumerate(raw_lines, start=1):
            if lineno in lines_to_skip:
                stripped = line.rstrip('\r\n')
                newline = line[len(stripped):] or '\n'
                f.write('$ read_bdf_safe skipped invalid card: ' + stripped + newline)
            else:
                f.write(line)
    return sidecar


def _write_duplicate_property_sidecar(path, encoding, cleanup):
    """Write a temporary BDF with duplicate property IDs made pyNastran-unique."""
    d, base = os.path.split(os.path.abspath(path))
    root, ext = os.path.splitext(base)
    sidecar = os.path.join(d, root + '.pyn_propids' + (ext or '.bdf'))

    with open(sidecar, 'w', encoding=encoding, newline='') as f:
        for lineno, line in enumerate(cleanup['raw_lines'], start=1):
            f.write(cleanup['rewritten'].get(lineno, line))
    return sidecar


def _is_prod_missing_a_error(exc):
    message = str(exc)
    if 'PROD' not in message.upper():
        return False
    return bool(_PROD_FIELD_MISSING_A_RE.search(message))


# 与几何/坐标提取无关、但已知会让 pyNastran 崩溃或纯属求解器参数的卡片。
# 默认在读取前禁用（禁用后 pyNastran 把它们当作 reject 卡片存原文，不解析），
# L1 只要节点/单元/坐标系,这些卡片一律用不到。
#   MDLPRM: MSC 模型参数卡。pyNastran 1.4.x 的 _add_mdlprm_object 有个必崩的断言
#           (assert self.model.mdlprm is None) —— 只要文件里出现第二张 MDLPRM 且键
#           重复(如两次 OFFDEF),就抛 AssertionError: MDLPRM OFFDEF LROFF。
_DEFAULT_DISABLE_CARDS = ('MDLPRM',)


def read_bdf_safe(bdf_filename, xref=True, punch=False, debug=False,
                  model=None, disable_cards=_DEFAULT_DISABLE_CARDS, **read_kwargs):
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
        m = model if model is not None else BDF(debug=debug)
        if disable_cards:
            m.disable_cards(list(disable_cards))
        return m

    def _record_warning(target_model, message):
        warnings = list(getattr(target_model, 'read_bdf_safe_warnings', []))
        warnings.append(message)
        target_model.read_bdf_safe_warnings = warnings

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
        if disable_cards:
            m2.disable_cards(list(disable_cards))
        try:
            m2.read_bdf(sidecar, xref=xref, punch=punch, encoding='utf-8', **read_kwargs)
            return m2
        finally:
            try:
                os.remove(sidecar)
            except OSError:
                pass
    except SyntaxError as exc:
        if not _is_prod_missing_a_error(exc):
            raise

        cleanup = _scan_invalid_prod_cleanup(bdf_filename, enc)
        if not cleanup or not cleanup['invalid_prod_pids']:
            raise

        sidecar = _write_cleanup_sidecar(bdf_filename, enc, cleanup)
        warning = (
            'read_bdf_safe: skipped invalid PROD cards missing A for PID(s) {} '
            'and {} dependent CROD element(s) while loading {}'
        ).format(
            ', '.join(cleanup['invalid_prod_pids']),
            len(cleanup['dependent_crod_eids']),
            os.path.basename(bdf_filename),
        )
        sys.stderr.write(warning + '\n')

        m2 = BDF(debug=debug)
        if disable_cards:
            m2.disable_cards(list(disable_cards))
        _record_warning(m2, warning)
        try:
            m2.read_bdf(sidecar, xref=xref, punch=punch, encoding=enc, **read_kwargs)
            return m2
        finally:
            try:
                os.remove(sidecar)
            except OSError:
                pass
    except Exception as exc:
        if not _is_duplicate_ids_error(exc):
            raise

        cleanup = _scan_duplicate_property_cleanup(bdf_filename, enc)
        if not cleanup:
            raise

        sidecar = _write_duplicate_property_sidecar(bdf_filename, enc, cleanup)
        details = ', '.join(cleanup['renumbered']) or 'none'
        warning = (
            'read_bdf_safe: renumbered duplicate property PID(s) {} ({})'
        ).format(
            ', '.join(str(pid) for pid in cleanup['duplicate_pids']),
            details,
        )
        if cleanup['skipped_same_type_count']:
            warning += '; skipped {} same-type duplicate property card(s)'.format(
                cleanup['skipped_same_type_count'])
        warning += ' while loading {}'.format(os.path.basename(bdf_filename))
        sys.stderr.write(warning + '\n')

        m2 = BDF(debug=debug)
        if disable_cards:
            m2.disable_cards(list(disable_cards))
        _record_warning(m2, warning)
        try:
            m2.read_bdf(sidecar, xref=xref, punch=punch, encoding=enc, **read_kwargs)
            return m2
        finally:
            try:
                os.remove(sidecar)
            except OSError:
                pass
