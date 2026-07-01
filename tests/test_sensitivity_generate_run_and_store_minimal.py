from webapi.models import SensitivityGenerateRunAndStoreRequest


def test_generate_run_and_store_request_accepts_minimal_payload():
    body = SensitivityGenerateRunAndStoreRequest(
        project_id=5,
        batch_no="1",
        input_inp_name="static_fem_with_part.inp",
        interactive=True,
        async_submit=False,
    )

    assert body.project_id == 5
    assert body.input_inp_name == "static_fem_with_part.inp"
    assert body.step is None
    assert body.instances == []
    assert body.field_prefix is None
    assert body.response_component is None
    assert body.position is None
