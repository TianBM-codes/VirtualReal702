#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ingest.py — Layer 2: Preprocessing (Pure Python)

This script reads Layer 1 (L1) faithful dumps and generates Layer 2 (L2) 
processing data necessary for Layer 3 (L3) rendering. 

Key operations:
1. Global coordinates computation (local coords * instance transform).
2. Surface extraction (removing shared internal faces).
3. Triangulation (polygons to triangles).
4. Smooth normals computation (area-weighted).
5. Flattening into [R, 3, 3] Triangle Soup.
6. (Placeholder) Octree generation for bounding box coarse filtering.

Usage:
    python src/ingest.py --workspace /path/to/workspace
"""

import argparse
import os
import sqlite3
import h5py
import numpy as np
from collections import defaultdict
import time

def parse_args():
    parser = argparse.ArgumentParser(description="L2 Preprocessing")
    parser.add_argument("--workspace", required=True, help="Path to the ODB workspace")
    return parser.parse_args()

def mkdirs(path):
    if not os.path.exists(path):
        os.makedirs(path)

def get_instance_transform(asm_h5, inst_name):
    """Retrieves the 4x4 transform matrix for an instance."""
    path = f"instances/{inst_name}/transform"
    if path in asm_h5:
        return asm_h5[path][:]
    return np.eye(4, dtype=np.float64)

def compute_global_coords(local_coords, transform):
    """Applies the 4x4 homogenous transform to [N, 3] local coordinates."""
    N = local_coords.shape[0]
    # Convert to homogenous coords [N, 4]
    homogenous = np.ones((N, 4), dtype=np.float64)
    homogenous[:, :3] = local_coords
    
    # Apply transform: (T @ coords.T).T
    global_coords = (transform @ homogenous.T).T
    return global_coords[:, :3].astype(np.float32)

ELEM_TYPE_CODE = {
    'S3': 0, 'S3R': 0, 'S6': 0,
    'S4': 1, 'S4R': 1, 'S4R5': 1, 'S8R': 1, 'S8R5': 1,
    'C3D4': 2, 'C3D4H': 2,
    'C3D6': 3, 'C3D6H': 3,
    'C3D8': 4, 'C3D8R': 4, 'C3D8H': 4, 'C3D8RH': 4,
    'C3D10': 5, 'C3D10M': 5, 'C3D10H': 5,
    'C3D15': 6, 'C3D15H': 6,
    'C3D20': 7, 'C3D20R': 7, 'C3D20H': 7, 'C3D20RH': 7,
}

def extract_surface_and_triangulate(geom_h5):
    """
    Extracts surface faces by finding faces that belong to only one element.
    Triangulates polygons (quads) using a fan pattern.
    
    Returns:
    - all_tri_node_rows: [Tf, 3]
    - all_tri_elem_row: [Tf]
    - all_tri_elem_type: [Tf]
    - all_tri_face_idx: [Tf]
    - surface_face_rows: [Sf] indices pointing into Tf
    """
    if "elements" not in geom_h5:
        return [], [], [], [], []
        
    face_owners = defaultdict(list)

    for etype_str in geom_h5["elements"]:
        etype_grp = geom_h5[f"elements/{etype_str}"]
        if "face_node_conn" not in etype_grp:
            continue

        face_node_conn = etype_grp["face_node_conn"][:]
        face_elem_idx = etype_grp["face_elem_idx"][:]
        face_seq = etype_grp["face_seq"][:]

        etype_code = ELEM_TYPE_CODE.get(etype_str, 0)

        Mf = face_node_conn.shape[0]
        for i in range(Mf):
            nodes = [n for n in face_node_conn[i] if n != -1]
            if len(nodes) < 3:
                continue

            key = frozenset(nodes)
            face_owners[key].append({
                "elem_row": int(face_elem_idx[i]),
                "type_code": etype_code,
                "etype_str": etype_str,
                "face_seq": int(face_seq[i]),
                "nodes": nodes
            })
            
    all_tri_node_rows = []
    all_tri_elem_row = []
    all_tri_elem_type = []
    all_tri_etype_str = []
    all_tri_face_idx = []
    surface_face_rows = []

    current_tri_idx = 0
    for key, owners in face_owners.items():
        is_surface = len(owners) == 1
        owner = owners[0]
        nodes = owner["nodes"]
        n = len(nodes)

        for i in range(1, n - 1):
            all_tri_node_rows.append([nodes[0], nodes[i], nodes[i+1]])
            all_tri_elem_row.append(owner["elem_row"])
            all_tri_elem_type.append(owner["type_code"])
            all_tri_etype_str.append(owner["etype_str"].encode("ascii"))
            all_tri_face_idx.append(owner["face_seq"])
            if is_surface:
                surface_face_rows.append(current_tri_idx)
            current_tri_idx += 1

    return (
        np.array(all_tri_node_rows, dtype=np.int32),
        np.array(all_tri_elem_row, dtype=np.int32),
        np.array(all_tri_elem_type, dtype=np.uint8),
        np.array(all_tri_etype_str, dtype="S8"),
        np.array(all_tri_face_idx, dtype=np.int32),
        np.array(surface_face_rows, dtype=np.int32)
    )

def compute_smooth_normals(coords_global, tri_node_rows):
    """
    Computes area-weighted smooth normals for all nodes.
    Returns: [N, 3] float32
    """
    N = coords_global.shape[0]
    node_normals = np.zeros((N, 3), dtype=np.float32)
    
    if len(tri_node_rows) == 0:
        return node_normals
        
    p0 = coords_global[tri_node_rows[:, 0]]
    p1 = coords_global[tri_node_rows[:, 1]]
    p2 = coords_global[tri_node_rows[:, 2]]
    
    # Cross product gives 2x the area of the triangle and the orthogonal vector
    face_normals_unnormalized = np.cross(p1 - p0, p2 - p0)
    
    # Add face normal to each of its vertices (this accomplishes area weighting)
    # Using np.add.at to handle duplicate indices safely
    np.add.at(node_normals, tri_node_rows[:, 0], face_normals_unnormalized)
    np.add.at(node_normals, tri_node_rows[:, 1], face_normals_unnormalized)
    np.add.at(node_normals, tri_node_rows[:, 2], face_normals_unnormalized)
    
    # Normalize
    norms = np.linalg.norm(node_normals, axis=1, keepdims=True)
    # Avoid division by zero
    norms = np.where(norms > 1e-12, norms, 1.0)
    
    return node_normals / norms

def process_instance(workspace, db_conn, asm_h5, inst_name):
    logger.info(f"Processing instance: {inst_name} ...")
    t0 = time.time()
    
    geom_path = os.path.join(workspace, "l1", "geometry", f"{inst_name}.h5")
    if not os.path.exists(geom_path):
        logger.warning(f"  WARNING: {geom_path} not found.")
        return
        
    l2_geom_dir = os.path.join(workspace, "l2", "geometry")
    l2_render_dir = os.path.join(workspace, "l2", "render")
    mkdirs(l2_geom_dir)
    mkdirs(l2_render_dir)
    
    surface_h5_path = os.path.join(l2_geom_dir, f"{inst_name}_surface.h5")
    render_h5_path = os.path.join(l2_render_dir, f"{inst_name}_render.h5")
    
    with h5py.File(geom_path, "r") as f_in:
        local_coords = f_in["nodes/coords"][:]
        labels = f_in["nodes/labels"][:]
        
        # 1. Global Coordinates
        transform = get_instance_transform(asm_h5, inst_name)
        coords_global = compute_global_coords(local_coords, transform)
        
        # 2 & 3. Surface Extraction & Triangulation
        tri_node_rows, tri_elem_row, tri_elem_type, tri_etype_str, tri_face_idx, surface_face_rows = extract_surface_and_triangulate(f_in)
        
        # 4. Smooth Normals (Computed using surface faces only, or all faces? Usually surface faces are enough for rendering)
        # But to have node_normals for all nodes, we could use all faces. Let's use all faces to match area weighting over the whole geometry, or just surface faces.
        # Actually, smooth normals for rendering only need surface triangles.
        if len(surface_face_rows) > 0:
            surf_tri_node_rows = tri_node_rows[surface_face_rows]
        else:
            surf_tri_node_rows = np.array([], dtype=np.int32)
            
        normals_global = compute_smooth_normals(coords_global, surf_tri_node_rows)
        
    Tf = len(tri_node_rows)
    Sf = len(surface_face_rows)
    logger.info(f"  Extracted {Tf} total triangles, {Sf} surface triangles.")
    
    # 5. Build Triangle Soup [Rf, 3, 3] from surface faces
    if Sf > 0:
        render_positions = coords_global[surf_tri_node_rows] # [Sf, 3, 3]
        render_normals = normals_global[surf_tri_node_rows]  # [Sf, 3, 3]
        render_face_idx = np.arange(Sf, dtype=np.int32)
        
        surf_tri_elem_row = tri_elem_row[surface_face_rows]
        surf_tri_elem_type = tri_elem_type[surface_face_rows]
        surf_tri_etype_str = tri_etype_str[surface_face_rows]
    else:
        render_positions = np.zeros((0, 3, 3), dtype=np.float32)
        render_normals = np.zeros((0, 3, 3), dtype=np.float32)
        render_face_idx = np.zeros(0, dtype=np.int32)
        surf_tri_etype_str = np.zeros(0, dtype="S8")
        
    # --- Write l2/geometry/<inst>_surface.h5 ---
    with h5py.File(surface_h5_path, "w") as f_surf:
        nodes_grp = f_surf.create_group("nodes")
        nodes_grp.create_dataset("labels", data=labels)
        nodes_grp.create_dataset("coords_global", data=coords_global)
        nodes_grp.create_dataset("normals", data=normals_global)
        
        # Write topology maps (all faces)
        faces_grp = f_surf.create_group("faces")
        if Tf > 0:
            faces_grp.create_dataset("tri_node_rows", data=tri_node_rows)
            faces_grp.create_dataset("tri_elem_row", data=tri_elem_row)
            faces_grp.create_dataset("tri_face_idx", data=tri_face_idx)
            faces_grp.create_dataset("tri_elem_type", data=tri_elem_type)
            
        surf_faces_grp = f_surf.create_group("surface_faces")
        if Sf > 0:
            surf_faces_grp.create_dataset("face_rows", data=surface_face_rows)
            
    # --- Write l2/render/<inst>_render.h5 ---
    with h5py.File(render_h5_path, "w") as f_rend:
        render_grp = f_rend.create_group("render")
        # Direct mmap-friendly arrays
        render_grp.create_dataset("positions", data=render_positions)
        render_grp.create_dataset("normals", data=render_normals)
        render_grp.create_dataset("render_face_idx", data=render_face_idx)
        
        if Sf > 0:
            render_grp.create_dataset("source_elem_row", data=surf_tri_elem_row)
            render_grp.create_dataset("source_node_rows", data=surf_tri_node_rows)
            render_grp.create_dataset("source_elem_type", data=surf_tri_elem_type)
            render_grp.create_dataset("source_elem_etype", data=surf_tri_etype_str)
            
        # TODO: 6. Octree Generation (Placeholder)
        # octree_grp = f_rend.create_group("octree")
        # ... logic to build Octree bounds ...
        
    # Update manifest.db
    db_conn.execute("""
        CREATE TABLE IF NOT EXISTS l2_instances (
            instance_name TEXT PRIMARY KEY,
            surface_path TEXT,
            render_path TEXT,
            surface_face_count INTEGER,
            render_face_count INTEGER,
            edge_count INTEGER,
            partition_count INTEGER
        )
    """)
    db_conn.execute("""
        INSERT OR REPLACE INTO l2_instances 
        (instance_name, surface_path, render_path, surface_face_count, render_face_count, edge_count, partition_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        inst_name, 
        f"l2/geometry/{inst_name}_surface.h5", 
        f"l2/render/{inst_name}_render.h5", 
        Sf, Sf, 0, 1 # Placeholders for edge/partition counts
    ))
    db_conn.commit()
    
    t1 = time.time()
    logger.info(f"  Done in {t1-t0:.2f}s.")

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

def main():
    args = parse_args()
    workspace = args.workspace
    
    if not os.path.exists(workspace):
        logger.error(f"Workspace {workspace} does not exist.")
        return
        
    db_path = os.path.join(workspace, "manifest.db")
    asm_path = os.path.join(workspace, "l1", "assembly.h5")
    
    if not os.path.exists(db_path) or not os.path.exists(asm_path):
        logger.error("L1 outputs (manifest.db or assembly.h5) not found. Run L1 extraction first.")
        return

    logger.info(f"=== Layer 2 Preprocessing ===")
    logger.info(f"  Workspace: {workspace}")
    
    odb_id = os.path.basename(os.path.normpath(workspace))
    # Note: L3 registry.db is global, usually outside the workspace.
    # Assuming standard structure: /data/registry.db and /data/<odb_id>/
    registry_db_path = os.path.join(os.path.dirname(os.path.normpath(workspace)), "registry.db")
    
    # Update status to l2_running
    if os.path.exists(registry_db_path):
        try:
            with sqlite3.connect(registry_db_path, timeout=5.0) as reg_conn:
                reg_conn.execute("PRAGMA journal_mode=WAL;")
                reg_conn.execute("UPDATE odb_jobs SET status='l2_running', l2_started_at=datetime('now') WHERE odb_id=?", (odb_id,))
                reg_conn.commit()
        except Exception as e:
            logger.warning(f"Could not update registry.db to l2_running: {e}")
    
    # Must use WAL mode if L3 might be running
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    
    # Get all instances from L1
    instances = conn.execute("SELECT instance_name FROM instances").fetchall()
    
    success = True
    try:
        with h5py.File(asm_path, "r") as asm_h5:
            for row in instances:
                process_instance(workspace, conn, asm_h5, row[0])
    except Exception as e:
        logger.error(f"Layer 2 failed: {e}", exc_info=True)
        success = False
    finally:
        conn.close()
        
    if os.path.exists(registry_db_path):
        try:
            with sqlite3.connect(registry_db_path, timeout=5.0) as reg_conn:
                reg_conn.execute("PRAGMA journal_mode=WAL;")
                new_status = 'ready' if success else 'l1_done'
                reg_conn.execute(f"UPDATE odb_jobs SET status=?, l2_done_at=datetime('now') WHERE odb_id=?", (new_status, odb_id))
                reg_conn.commit()
        except Exception as e:
            logger.warning(f"Could not update registry.db final status: {e}")

    logger.info("=== Layer 2 Complete ===")

if __name__ == "__main__":
    main()
