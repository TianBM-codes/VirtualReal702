"""L2 邻接生成与 SciPy 连通域划分的回归测试。"""

import numpy as np

from src.l2.ingest import (
    ELEM_KIND_MEMBRANE,
    ELEM_KIND_SHELL,
    ELEM_KIND_SOLID,
    build_domain_ids,
    compute_elem_adjacency,
)
from src.l3.services.result_service import _union_find_domains


def test_compute_elem_adjacency_mixed_tri_quad_and_angles():
    coords = np.array([
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [1.0, 1.0, 0.0],
        [0.0, 1.0, 0.0],
        [2.0, 0.0, 0.0],
        [2.0, 1.0, 0.0],
        [1.0, 1.0, 1.0],
    ], dtype=np.float32)
    faces = np.array([
        [0, 1, 2, 3],       # quad, normal +Z
        [1, 4, 5, 2],       # quad, normal +Z; shares edge 1-2
        [2, 5, 6, -1],      # triangle; shares edge 2-5 at 90 degrees
    ], dtype=np.int32)
    elem_idx = np.array([10, 11, 12], dtype=np.int32)

    src, dst, angle = compute_elem_adjacency(faces, elem_idx, coords)

    np.testing.assert_array_equal(src, [10, 11])
    np.testing.assert_array_equal(dst, [11, 12])
    np.testing.assert_allclose(angle, [0.0, 90.0], atol=1e-5)


def test_compute_elem_adjacency_non_manifold_and_same_element_filter():
    coords = np.array([
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, -1.0, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float32)
    faces = np.array([
        [0, 1, 2],
        [1, 0, 3],
        [0, 1, 4],
    ], dtype=np.int32)
    # The first two faces belong to the same solid element, so only pairs
    # crossing to element 21 remain from the three-face non-manifold edge.
    elem_idx = np.array([20, 20, 21], dtype=np.int32)

    src, dst, angle = compute_elem_adjacency(faces, elem_idx, coords)

    np.testing.assert_array_equal(src, [20, 20])
    np.testing.assert_array_equal(dst, [21, 21])
    assert angle.shape == (2,)


def test_build_domain_ids_respects_section_kind_and_feature_angle():
    section = np.array([1, 1, 1, 1, 2, 2, 2], dtype=np.int32)
    kind = np.array([
        ELEM_KIND_SOLID,
        ELEM_KIND_SOLID,
        ELEM_KIND_SHELL,
        ELEM_KIND_MEMBRANE,
        ELEM_KIND_SHELL,
        ELEM_KIND_SHELL,
        ELEM_KIND_SOLID,
    ], dtype=np.uint8)
    src = np.array([0, 1, 2, 3, 4, 5], dtype=np.int32)
    dst = np.array([1, 2, 3, 4, 5, 6], dtype=np.int32)
    angle = np.array([80.0, 0.0, 10.0, 0.0, 30.0, 0.0], dtype=np.float32)

    domains = build_domain_ids(
        section, kind, src, dst, angle, feature_angle_deg=20.0)

    # 0-1: solids connect regardless of angle; 1-2: mixed kinds do not;
    # 2-3: shell/membrane connect; 3-4: different sections do not;
    # 4-5: above threshold; 5-6: mixed kinds.
    np.testing.assert_array_equal(domains, [0, 0, 1, 1, 2, 3, 4])


def test_l3_dynamic_domain_partition_matches_l2():
    section = np.array([0, 0, 0, 1], dtype=np.int32)
    kind = np.array([
        ELEM_KIND_SHELL,
        ELEM_KIND_SHELL,
        ELEM_KIND_MEMBRANE,
        ELEM_KIND_SOLID,
    ], dtype=np.uint8)
    src = np.array([0, 1, 2], dtype=np.int32)
    dst = np.array([1, 2, 3], dtype=np.int32)
    angle = np.array([5.0, 25.0, 0.0], dtype=np.float32)

    expected = build_domain_ids(
        section, kind, src, dst, angle, feature_angle_deg=10.0)
    actual = _union_find_domains(
        section, kind, src, dst, angle, feature_angle_deg=10.0)

    np.testing.assert_array_equal(actual, expected)
