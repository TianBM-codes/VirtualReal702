from services.model_update.analysis import sensitivity_service


def test_build_src_merge_parameter_rows_maps_set_suffix_field_to_single_parameter():
    parameter_rows = [
        {
            "parameter_name": "T1",
            "quantity_code": "T",
            "set_name": "SET_149",
            "set_type": "ELSET",
            "set_scope": "PART",
            "instance_name": None,
            "part_name": "P1",
        }
    ]

    rows = sensitivity_service._build_src_merge_parameter_rows(
        parameter_rows=parameter_rows,
        field_prefix="d_U_",
        source_field_names=["d_U_T_SET_149", "d_UR_T_SET_149"],
    )

    assert len(rows) == 149
    assert rows[148]["parameter_name"] == "T1"
    assert rows[148]["set_name"] == "SET_149"


def test_build_src_merge_parameter_rows_keeps_direct_index_mapping():
    parameter_rows = [
        {"parameter_name": "T1", "set_name": "SET_A"},
        {"parameter_name": "T2", "set_name": "SET_B"},
    ]

    rows = sensitivity_service._build_src_merge_parameter_rows(
        parameter_rows=parameter_rows,
        field_prefix="d_U_T",
        source_field_names=["d_U_T1", "d_U_T2"],
    )

    assert len(rows) == 2
    assert rows[0]["parameter_name"] == "T1"
    assert rows[1]["parameter_name"] == "T2"
