# ODB Service Design - Project Overview & Context

This repository contains the design and early-stage implementation of a backend service for visualizing Abaqus ODB (Output Database) files. The system follows a three-layer architecture to handle large-scale FEM data efficiently.

## 🤝 协作约定 (Collaboration Conventions)
**用户背景**：用户不熟悉有限元、HDF5、FastAPI、Three.js 等技术栈。在回答问题、解释设计决策、说明代码改动时，需要：
- 用大白话解释"为什么这么做"，不只说"怎么做"
- 新概念第一次出现时主动解释
- 实现完成后说明整个流程是什么，前后端各做什么
- 遇到技术取舍时说清楚利弊，而不是只给结论
- 科普性内容写进 `FEM-Viewer-Primer.md`，流程/设计写进对应 `docs/` 文档

## 🔄 Git 工作流 (Git Workflow)
本项目通过 Git+SSH 与开发者本地机器同步。开发者在本地 Windows 运行服务，不在此 Linux 服务器上运行。
因此，**每次修改完代码必须提交并推送**，以便开发者在本地通过 `git pull` 获取最新代码。
```bash
git add <files>
git commit -m "..."
git push
```
除非用户明确说"只提交不推"，否则每次 commit 后必须 push。

## 🏗 Architecture (v5)

The project is divided into three distinct layers to separate concerns and manage dependencies (like Abaqus licenses).

### Layer 1: ODB Faithful Dump
- **Goal:** Extract raw data from `.odb` files and store it in a structured HDF5/SQLite format without processing.
- **Tools:** 
    - `src/l1/abaqus_dump.py`: Runs in Abaqus Python (v2.7) environment to extract data to temporary `.npy` files.
    - `src/l1/l1_pack.py`: Runs in standard Python 3 to pack temporary files into finalized HDF5 files and populate `manifest.db`.
- **Output:** `l1/assembly.h5`, `l1/geometry/<inst>.h5`, `l1/results/<step>__<field>.h5`, and `manifest.db`.

### Layer 2: Preprocessing (Partially Implemented)
- **Goal:** Generate rendering and interaction data (surface extraction, triangulation, octree, feature edges).
- **Planned Tool:** `src/l2/ingest.py` (Pure Python 3).
- **Output:** `l2/geometry/<inst>_surface.h5`, `l2/render/<inst>_render.h5`.

### Layer 3: API Service (Skeleton Implemented)
- **Goal:** Provide a FastAPI-based REST API to serve geometry (binary chunks) and results (sparse arrays).
- **Framework:** FastAPI + Gunicorn (multi-process worker model).
- **Key Features:** `ModelIndex` for fast label-to-row mapping using `numpy.searchsorted`.

---

## 🛠 Technology Stack

- **Languages:** Python 2.7 (Abaqus env), Python 3.x (Service/Packing).
- **Data Storage:** 
    - **HDF5 (h5py):** Large multi-dimensional arrays (mesh, results).
    - **SQLite (sqlite3):** Metadata, job registry, and cross-layer routing (`manifest.db`).
- **Processing:** NumPy, SciPy (cKDTree for spatial queries).
- **Web:** FastAPI, Uvicorn, Gunicorn.
- **Frontend:** Three.js (planned, not in this repo).

---

## 🚀 Development Workflow

### 1. Layer 1 Extraction (Requires Abaqus License)
```bash
# Phase 1: Dump to temporary files
abaqus python src/l1/abaqus_dump.py --odb path/to/model.odb --out /data/workspace_id/

# Phase 2: Pack to HDF5 and SQLite
python src/l1/l1_pack.py --workspace /data/workspace_id/
```

### 2. Layer 2 Preprocessing
(Partially implemented: surface extraction & triangulation)
```bash
python src/l2/ingest.py --workspace /data/workspace_id/
```

### 3. Layer 3 Service
(Core skeleton, health, pick, and bbox endpoints implemented)
```bash
gunicorn src.l3.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

---

## 📖 Key Documentation

- **[ODB-Service-Architecture.md](./ODB-Service-Architecture.md):** The primary design document (v5), covering the three-layer architecture and data models.
- **[docs/l3/L3-API-Contract.md](./docs/l3/L3-API-Contract.md):** Detailed API endpoint specifications.
- **[docs/l3/L3-FastAPI-Scaffold-Design.md](./docs/l3/L3-FastAPI-Scaffold-Design.md):** Planned structure for the FastAPI backend.
- **[FEM-Viewer-Primer.md](./FEM-Viewer-Primer.md):** Overview of FEM concepts for developers.

---

## ⚖️ Development Conventions

- **Three-Layer Separation:** Never merge L1 (raw) and L2 (processed) data into the same files. Use `manifest.db` for routing.
- **Instance-Level Scoping:** Abaqus labels (node/element) are unique only within an `Instance`. All queries must use `(instance_name, label)`.
- **Binary Payloads:** Large arrays must be served as `application/octet-stream` with metadata in JSON or HTTP headers.
- **Multi-Process Safety:** The `job-runner` (L1/L2) must be a separate process from the L3 Web Service to avoid SQLite/HDF5 locking issues.
- **CoW Optimization:** Load static indices and label maps into memory before forking Gunicorn workers to maximize Copy-on-Write (CoW) memory sharing.

---

## 📅 Roadmap / TODOs
- [x] Implement core `ingest.py` (Layer 2).
- [ ] Implement Octree and Feature Edges in L2.
- [x] Implement FastAPI scaffold and core queries (Layer 3).
- [ ] Implement binary chunk streaming endpoints (Layer 3).
- [ ] Verify shell element nodal extrapolation (Blocker A).
- [ ] Verify `localCsys` attribute names for transform matrices (Blocker B).
