import numpy as np

from src.inp.model import Instance, Rotation
from src.inp.resolver import _build_transform


def test_build_transform_uses_second_axis_point_not_absolute_vector():
    inst = Instance(
        name="I1",
        part_name="P1",
        translation=(10.0, 0.0, 0.0),
        rotation=Rotation(
            center=(10.0, 0.0, 0.0),
            axis=(10.0, 1.0, 0.0),
            angle_deg=90.0,
        ),
    )

    transform = _build_transform(inst)
    point = np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float64)
    transformed = transform @ point

    assert np.allclose(transformed[:3], [10.0, 0.0, -1.0], atol=1e-8)
