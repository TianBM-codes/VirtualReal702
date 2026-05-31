from types import SimpleNamespace

from services.model_update.analysis.nastran_sol200_service import (
    _clone_property_with_material,
    _material_copy_with_new_id,
)
from services.model_update.solver_prep.nastran_sol200 import (
    _build_response_lines,
    build_sol200_controls,
)


def test_build_sol200_controls_uses_plot_displacement_and_subcase_dessub_for_op2():
    lines = build_sol200_controls(
        {
            "result.target": "OP2",
            "dynamic.vectors": 8,
        }
    )

    assert "DISPLACEMENT(PLOT) = ALL" in lines
    assert "  DESSUB = 1" in lines


def test_build_sol200_controls_uses_formatted_dsaprt_when_csv_enabled(tmp_path):
    lines = build_sol200_controls(
        {
            "result.target": "OP2",
            "dynamic.vectors": 8,
        },
        sensitivity_csv_path=str(tmp_path / "sol200_sens.csv"),
    )

    assert "DSAPRT(FORMATTED,EXPORT,END=SENS)" in lines
    assert "PARAM,XYUNIT,52" in lines


def test_build_sol200_response_lines_allow_negative_lower_bound_and_disable_screening():
    lines = _build_response_lines(1, {"type": "FREQ", "name": "FREQ1", "mode_number": 1})

    assert "DCONSTR,1,1,-1.0E30,1.0E30" in lines
    assert "DSCREEN  FREQ    -1.0E30" in lines


def test_clone_property_with_material_updates_multimaterial_fields():
    prop = SimpleNamespace(pid=10, mid1=101, mid2=202, mid3=303, type="PCOMP")

    cloned = _clone_property_with_material(prop, new_pid=20, new_mid=404)

    assert cloned.pid == 20
    assert cloned.mid1 == 404
    assert cloned.mid2 == 404
    assert cloned.mid3 is None


def test_material_copy_with_new_id_clears_mat1_g():
    material = SimpleNamespace(mid=10, g=123.0, type="MAT1")

    cloned = _material_copy_with_new_id(material, new_mid=20)

    assert cloned.mid == 20
    assert cloned.g is None
