# CLAUDE.md

**用中文回复用户。**

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 协作约定

**用户背景**：用户不熟悉有限元、HDF5、FastAPI、Three.js 等技术栈。在回答问题、解释设计决策、说明代码改动时，需要：
- 用大白话解释"为什么这么做"，不只说"怎么做"
- 新概念第一次出现时主动解释
- 实现完成后说明整个流程是什么，前后端各做什么
- 遇到技术取舍时说清楚利弊，而不是只给结论
- 科普性内容写进 `FEM-Viewer-Primer.md`，流程/设计写进对应 `docs/` 文档

## Project Overview

Backend service for visualizing Abaqus ODB (Output Database) finite element analysis simulation files. Handles large-scale FEM data (up to ~10M nodes, ~30 frames) and serves it to a Three.js frontend for 3D visualization.

**Implementation status**: L1 implemented; L2 substantially implemented (surface extraction + Triangle Soup + Feature Edges + Octree done, Partitioning missing); L3 core skeleton implemented (health + pick + bbox + frame-colors endpoints). L3 does not yet load or use L2 octree/feature-edge data.

**主设计文档**：`ODB-Service-Architecture.md`（v5，项目内最高权威）。所有实现细节、HDF5 schema、算法伪代码、manifest.db schema 均以该文档为准。遇到歧义时以该文档为准，不以代码为准。

## Git 工作流

本项目托管在 GitHub 私有仓库：`https://github.com/syouro/odb-service-design`（`syouro/odb-service-design`）。

通过 Git+HTTPS 与开发者本地机器同步。**每次修改完必须提交并推送**，开发者通过 `git pull` 取最新代码。

```bash
git add <files>
git commit -m "..."
git push
```

除非明确说"只提交不推"，否则每次 commit 后必须 push。

## 测试约定

**不要在本机（codex 服务器）上运行测试。** 测试由开发者在本地机器上执行，通过 `git pull` 取代码后运行：

```bash
python3 -m pytest tests/ -v
```

写完测试代码后直接 commit + push，等开发者反馈结果。

## Running the Code

There is no build system. All execution is manual.

**L1 Extraction (requires Abaqus license):**
```bash
# Phase 1: Run under Abaqus Python 2.7 — dumps ODB to temporary .npy files
abaqus python src/l1/abaqus_dump.py --odb /path/to/model.odb --out /data/<odb_id>/

# Phase 2: Run under Python 3 — packs .npy → HDF5 + manifest.db
python src/l1/l1_pack.py --workspace /data/<odb_id>/
```

**L2 preprocessing and L3 service are not yet implemented.** When built:
```bash
python src/l2/ingest.py --workspace /data/<odb_id>/          # L2 (planned)
gunicorn src.l3.main:app -w 4 -k uvicorn.workers.UvicornWorker  # L3 (planned)
```

No tests or linting infrastructure exists yet.

## Three-Layer Architecture

### L1 — Faithful ODB Dump (`src/l1/abaqus_dump.py` + `src/l1/l1_pack.py`)
Two-phase extraction: `abaqus_dump.py` runs under Abaqus Python 2.7 to extract data to `.npy` files; `l1_pack.py` runs under standard Python 3 to pack them into HDF5 + SQLite. Data is stored as-is — no processing, no coordinate transforms applied. Output structure:
```
/data/<odb_id>/
  l1/
    assembly.h5                      (instances, transforms, assembly sets)
    geometry/<inst>.h5               (nodes, elements, corner connectivity)
    geometry/<inst>_highorder.h5     (full high-order connectivity)
    sets/sets.h5                     (node/element set memberships)
    results/<step>__<field>.h5       (NODAL, INTEGRATION_POINT, ELEMENT_NODAL)
  manifest.db                        (routing & metadata)
```

### L2 — Preprocessing (`src/l2/ingest.py`, not yet implemented)
One-time batch job (pure Python 3) that converts L1 raw data into rendering-ready buffers. Key operations: apply instance transforms, linearize high-order elements (use corner nodes only), triangulate and de-duplicate faces to extract surface geometry, compute feature edges (boundary + fold ≥30°), build octree spatial index (`max_depth=8`, leaf threshold ≤1000 triangles).

### L3 — Frontend Service (`src/l3/`, not yet fully implemented)
Continuous FastAPI + Gunicorn multi-worker service. Reads L1 HDF5 for results and L2 HDF5 for geometry; only writes to `manifest.db` (user sets). See `docs/l3/` for full API contract and scaffold design.

## Key Data Conventions

**Label scoping:** Node/element labels are only unique within an Instance. Always use `(instance_name, label)` pairs, never bare label numbers.

**Triangle Soup geometry:** Render buffers are `[Rf, 3, 3] float32` — fully exploded, no index buffer. Each face carries its `render_face_idx` (globally stable per instance).

**Label→index mapping:** Uses sorted numpy arrays + `numpy.searchsorted` (not dicts). Binary search over sorted label arrays, ~80 MB/instance in memory.

**Multi-frame HDF5 layout:** Results stored as `[num_frames, N, ...]` — enables single-IO access for both frame slices and node time-series.

**High-order elements:** Main geometry arrays use corner nodes only. Full connectivity (including mid-nodes) is in `_highorder.h5`.

**L1 is immutable after creation. L2 files are read-only by L3. L3 only writes `user_sets` table in `manifest.db`.**

## manifest.db Schema

Central routing registry — no filesystem scanning at runtime.

| Table | Key columns |
|---|---|
| `instances` | `instance_name, part_name, geom_path, bbox` |
| `element_type_dist` | `instance_name, elem_type, count, n_corner_nodes` |
| `steps` | `step_name, procedure (STATIC\|FREQUENCY\|DYNAMIC\|BUCKLE), num_frames` |
| `frames` | `step_name, frame_idx, frame_value, description` |
| `result_files` | `step_name, field_name, file_path, components, positions` |
| `result_blocks` | `step_name, field_name, instance_name, position, elem_type, h5_path` |
| `node_sets / element_sets` | `set_name, instance_name, h5_path` |
| `user_sets` | `set_name, set_scope, user_set_instances` (compressed BLOB) |

Job lifecycle tracked in manifest: `submitted → l1_running → l1_done → l2_running → ready`

## Key Design Decisions

- **No Abaqus at runtime:** L1 extraction requires Abaqus Python 2.7; L2/L3 use standard Python 3 only.
- **Concurrent safety:** HDF5/SQLite are single-writer. Job runner (L1/L2) is a separate process from the L3 web service; multiple L3 Gunicorn workers can read in parallel (HDF5 is multi-reader safe). User sets stored as compressed BLOBs in SQLite to avoid HDF5 multi-process write conflicts.
- **Memory model:** L3 loads 2–3 active ODB workspaces (~2 GB each); Gunicorn workers share static numpy indices via copy-on-write fork.
- **No result caching in L3:** Reads directly from L1 HDF5 each request.

## Known Blockers

- **Blocker A (Shell Sections):** PoC needed for `getSubset(position=NODAL)` on shell mid-surface/bottom/top points in Abaqus Python API.
- **Blocker B (Transform Attributes):** Verify `localCsys.origin/.xAxis/.yAxis/.zAxis` attribute names in the target Abaqus version.

## Documentation Map

| File | Purpose |
|---|---|
| `ODB-Service-Architecture.md` | Comprehensive v5 design doc — authoritative reference for all layers |
| `FEM-Viewer-Primer.md` | FEM concepts, ODB structure, Three.js rendering relationship |
| `docs/l3/L3-API-Contract.md` | REST endpoint specifications |
| `docs/l3/Binary-Payload-Spec.md` | Binary envelope protocol for large array responses |
| `docs/l3/L3-Render-Assembly-Design.md` | How L3 assembles render buffers from L1/L2 data |
| `docs/l3/L3-Module-Boundary.md` | Router/Service/Repository responsibility separation |
| `docs/l1/abaqus_dump_guide.md` | Practical guide for running L1 extraction |
| `temp/` | Historical design iterations (v1–v4) and review notes; not authoritative |
