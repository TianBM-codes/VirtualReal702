"""Direction projection of symmetric stress/strain tensors; no numpy dependency."""

import math

COMPONENTS = ("11", "22", "33", "12", "13", "23")


def vector3(value, label="vector"):
    if len(value) != 3:
        raise ValueError("%s must have three components" % label)
    result = tuple(float(x) for x in value)
    if not all(math.isfinite(x) for x in result):
        raise ValueError("%s must be finite" % label)
    return result


def unit_vector(value):
    v = vector3(value, "direction")
    length = math.hypot(*v)
    if length == 0 or not math.isfinite(length):
        raise ValueError("direction must have finite nonzero length")
    return tuple(x / length for x in v)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _matrix_vector(matrix, vector):
    return tuple(_dot(row, vector) for row in matrix)


def _rotation(matrix):
    if matrix is None:
        return ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
    if len(matrix) != 3:
        raise ValueError("local_to_global must be 3x3")
    r = tuple(vector3(row, "rotation row") for row in matrix)
    for i in range(3):
        for j in range(3):
            if not math.isclose(_dot(r[i], r[j]), float(i == j), abs_tol=1e-8):
                raise ValueError("local_to_global must be orthonormal")
    det = (r[0][0] * (r[1][1]*r[2][2]-r[1][2]*r[2][1])
           - r[0][1] * (r[1][0]*r[2][2]-r[1][2]*r[2][0])
           + r[0][2] * (r[1][0]*r[2][1]-r[1][1]*r[2][0]))
    if not math.isclose(det, 1., abs_tol=1e-8):
        raise ValueError("local_to_global must be a right-handed rotation")
    return r


def _tensor(components, *, engineering_shear=False):
    missing = set(COMPONENTS) - set(components)
    if missing:
        raise ValueError("Missing tensor components: %s" % sorted(missing))
    xx, yy, zz, xy, xz, yz = (float(components[k]) for k in COMPONENTS)
    if not all(math.isfinite(x) for x in (xx, yy, zz, xy, xz, yz)):
        raise ValueError("Tensor components must be finite")
    if engineering_shear:
        xy, xz, yz = xy / 2., xz / 2., yz / 2.
    return ((xx, xy, xz), (xy, yy, yz), (xz, yz, zz))


def project_stress(components, direction, *, local_to_global=None):
    """direction is a global plane normal; R columns are local axes in global space.

    Returns sigma_n=n.T sigma n, traction=sigma n, and shear traction magnitude.
    No missing-component or plane-stress assumption is made.
    """
    n = unit_vector(direction)
    r = _rotation(local_to_global)
    local_n = _matrix_vector(tuple(zip(*r)), n)
    local_t = _matrix_vector(_tensor(components), local_n)
    traction = _matrix_vector(r, local_t)
    normal = _dot(n, traction)
    shear = tuple(t - normal * ni for t, ni in zip(traction, n))
    return {"direction": list(n), "normal_stress": normal,
            "traction": list(traction), "shear_traction": list(shear),
            "shear_stress": math.hypot(*shear)}


def project_strain(components, direction, *, shear_convention, local_to_global=None):
    """Axial strain n.T epsilon n. Explicitly specify tensor or engineering shear."""
    if shear_convention not in ("tensor", "engineering"):
        raise ValueError("shear_convention must be tensor or engineering")
    n = unit_vector(direction)
    r = _rotation(local_to_global)
    local_n = _matrix_vector(tuple(zip(*r)), n)
    tensor = _tensor(components, engineering_shear=shear_convention == "engineering")
    return {"direction": list(n), "axial_strain": _dot(local_n, _matrix_vector(tensor, local_n)),
            "shear_convention": shear_convention}
