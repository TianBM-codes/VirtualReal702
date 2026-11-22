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


def test_named_elastic_and_thickness_fields_with_same_s4_suffix_stay_distinct():
    rows = [{"parameter_name": f"{quantity}@M{material}_S4", "quantity_code": quantity,
             "set_name": f"M{material}_S4"}
            for quantity in ["E", "T"] for material in [2, 3, 4, 5]]
    names = [f"d_U_{row['parameter_name'].replace('@', '_')}" for row in rows]
    result = sensitivity_service._build_src_merge_parameter_rows(
        parameter_rows=rows, field_prefix="d_U_", source_field_names=names)
    assert len(result) == 8
    assert {row["source_field_name"]: row["parameter_name"] for row in result} == {
        name: row["parameter_name"] for name, row in zip(names, rows)}
