from src.l3.services import result_service


def test_resolve_sensitivity_frame_alias_uses_embedded_frame():
    field, frame_idx = result_service._resolve_sensitivity_frame_alias(
        "SENSITIVITY_CLOUD__FRAME_0007",
        0,
    )

    assert field == "SENSITIVITY_CLOUD"
    assert frame_idx == 7


def test_resolve_sensitivity_frame_alias_keeps_regular_fields():
    field, frame_idx = result_service._resolve_sensitivity_frame_alias("U", 3)

    assert field == "U"
    assert frame_idx == 3
