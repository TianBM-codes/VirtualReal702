# BDF 读取常见报错与处理

> 更新时间：2026-07-08
>
> 记录 L1 用 pyNastran 读取 Nastran BDF 时遇到的坑，以及项目里的统一处理方式。
> **所有读 BDF 的地方都应改用 `src/l1/bdf_read.py` 的 `read_bdf_safe()`，不要直接调 `model.read_bdf()`。**

```python
from src.l1.bdf_read import read_bdf_safe
model = read_bdf_safe(bdf_path, xref=True)   # 编码/脏卡片都在里面兜住了
```

---

## 报错 1：`UnicodeEncodeError: 'gbk' codec can't encode character '�'`

**完整现象**

```
UnicodeEncodeError: 'gbk' codec can't encode character '�' in position 89: illegal multibyte sequence
    model.read_bdf  ...  _parse_primary_file_header
```

**根因**

看着像"编码不了"，其实是**解码失败**的连锁反应：

1. 中文 Windows 上 `open()` 的默认(locale)编码是 **gbk(cp936)**。
2. pyNastran 在真正解析前，先调 `_parse_primary_file_header()` 扫文件头的
   `$ pyNastran: ...` 提示行，这一步用的是**不带 encoding 的 `open(path, 'r')`**，
   也就是用 gbk 去读。
3. 若 BDF 实际是 **UTF-8**（含中文注释、`°`/`µ` 等符号，或带 UTF-8 BOM），gbk 解不出来
   → `UnicodeDecodeError` → pyNastran 转容错分支，把非法字节替换成 `�`（即 `�`），
   再把这些行打到日志。
4. 日志往 gbk 控制台一写，`�` 又编码不回 gbk → 抛出最终的 `UnicodeEncodeError`。

**关键坑：给 `read_bdf(..., encoding=...)` 传参数没用。** 因为出事的头部扫描那一步压根不看
`encoding`，永远用 locale 默认编码打开。所以同事直接传 `encoding='gbk'` 一样崩
（而且传 gbk 本身就是错方向——文件根本不是 gbk）。

**处理方式（已在 `read_bdf_safe` 实现）**

1. 先按 `utf-8-sig → utf-8 → gbk → latin-1` 探测文件真实编码并透传。Linux/mac
   直接过；Windows 上只要文件能被 gbk 解开也直接过。
2. 只有当头部扫描真的因 locale 崩溃（`UnicodeError`）时，才在源文件**同目录**写一个
   纯 ASCII 化的临时副本（非 ASCII 只在 `$` 注释里，换成 `?` 不影响几何；同目录是为了
   `INCLUDE` 相对路径仍可解析）再读，读完删除。

---

## 报错 2：`AssertionError: MDLPRM   OFFDEF   LROFF`

**完整现象**

```
bdf_interface/add_methods  line 235  in _add_mdlprm_object
AssertionError: MDLPRM   OFFDEF   LROFF
```

**根因**

`OFFDEF`/`LROFF` 本身是合法的 MDLPRM 键值，卡片能正常解析。崩的是 pyNastran 自身的 bug：

```python
def _add_mdlprm_object(self, mdlprm, allow_overwrites=False):
    if self.model.mdlprm is None:
        self.model.mdlprm = mdlprm
    else:
        for key, value in mdlprm.mdlprm_dict.items():
            if key in model_mdlprm_dict:
                assert self.model.mdlprm is None, self.model.mdlprm   # ← 必崩
```

当文件里出现**第二张 MDLPRM** 且键与已有的重复（如两次 `OFFDEF`）时，会进入 `else` 分支，
此时 `self.model.mdlprm` 显然不为 None，这句 `assert ... is None` 必然失败。报错信息里的
`MDLPRM OFFDEF LROFF` 就是已存那张卡的内容。这是 pyNastran 1.4.x 处理重复 MDLPRM 的缺陷，
不是文件真有问题。

**处理方式（已在 `read_bdf_safe` 实现）**

MDLPRM 是 MSC 求解器的模型参数卡，**L1 只要节点/单元/坐标系，根本用不到它**。所以读之前
先 `model.disable_cards(['MDLPRM'])` —— 禁用后 pyNastran 把它当作 reject 卡片存原文、
不再解析，也就不会触发那个断言。`read_bdf_safe` 的 `disable_cards` 参数默认就含 `MDLPRM`，
将来遇到别的"纯求解器参数、还会把 pyNastran 搞崩"的卡片，往这个默认列表里加即可。

---

## 尚未改造的调用点

报错出在 `src/l1`，已改的：`src/l1/bdf_pack.py`、`src/l1/op2_pack.py`。

以下仍是直接 `model.read_bdf(...)`，遇到同类文件会踩同样的坑，建议后续一并切到
`read_bdf_safe`：

- `services/model_update/analysis/bayesian_service.py`
- `services/model_update/analysis/nastran_sol200_service.py`（3 处）
- `services/model_update/importers/op2_service.py`
- 根目录 `BDFParserPyNastran.py`
