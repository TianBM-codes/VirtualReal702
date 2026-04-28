from pathlib import Path
from types import SimpleNamespace

from services.model_update.analysis import sensitivity_service


def test_rebuild_selected_parameters_from_inp_builds_in_memory_rows(monkeypatch, tmp_path: Path):
    inp_path = tmp_path / "demo.inp"
    inp_path.write_text("*Heading\n", encoding="utf-8")

    fake_model = SimpleNamespace(
        parameters={
            "T1": SimpleNamespace(scalar_value=0.01),
        },
        design_parameters=[
            SimpleNamespace(name="T1", order=1),
        ],
    )

    monkeypatch.setattr(sensitivity_service, "parse_inp", lambda path: fake_model)
    monkeypatch.setattr(
        sensitivity_service,
        "build_parameter_target_map",
        lambda model: {
            "T1": [
                {
                    "parameter_name": "T1",
                    "set_name": "SHELL1",
                    "set_type": "ELSET",
                    "set_scope": "PART",
                    "instance_name": None,
                    "part_name": "PART-1",
                    "source_keyword": "SHELL SECTION",
                    "component_name": "THICKNESS",
                }
            ]
        },
    )

    result = sensitivity_service._rebuild_selected_parameters_from_inp(
        project_id=1001,
        inp_path=str(inp_path),
    )

    assert result["selected_parameter_count"] == 1
    assert result["selected_parameters_preview"][0]["parameter_name"] == "T1"
    assert result["selected_parameters_preview"][0]["quantity_code"] == "T"
    assert result["optimization_parameter_rows"] == [
        {
            "id": 1,
            "parameter_group_name": "T1",
            "parameter_name": "T1",
            "quantity_code": "T",
            "selection_mode": "GLOBAL",
            "set_name": "SHELL1",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "PART-1",
            "element_label": None,
            "lower": 0.01,
            "upper": 0.01,
            "prob_id": 0,
            "scatter": sensitivity_service._DEFAULT_PARAMETER_SCATTER,
            "scalar_value": 0.01,
            "extra_json": {
                "source": "run_and_store_temp",
                "scalar_value": 0.01,
                "target_rows": [
                    {
                        "parameter_name": "T1",
                        "set_name": "SHELL1",
                        "set_type": "ELSET",
                        "set_scope": "PART",
                        "instance_name": None,
                        "part_name": "PART-1",
                        "source_keyword": "SHELL SECTION",
                        "component_name": "THICKNESS",
                    }
                ],
            },
        }
    ]
