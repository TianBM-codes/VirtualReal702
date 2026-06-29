from services.model_update.analysis import fem_correlation_service


def test_match_modal_modes_treats_fractional_mac_threshold_as_percent(monkeypatch):
    monkeypatch.setattr(
        fem_correlation_service,
        "_load_modal_correlation_rows",
        lambda project_id: [
            {
                "fem_mode_no": 1,
                "test_mode_no": 1,
                "mac": 69.0,
                "freq_fem": 31.8,
                "freq_test": 32.5,
                "freq_error_ratio": -0.0215,
                "flip": False,
            },
            {
                "fem_mode_no": 2,
                "test_mode_no": 2,
                "mac": 97.5,
                "freq_fem": 64.2,
                "freq_test": 63.9,
                "freq_error_ratio": 0.0047,
                "flip": True,
            },
        ],
    )

    payload = fem_correlation_service.match_modal_modes(
        18,
        mac_threshold=0.7,
        max_freq_error_ratio=0.2,
        method="greedy",
    )

    assert payload["mac_threshold"] == 70.0
    assert payload["summary"]["matched_pair_count"] == 1
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["fem_mode_no"] == 2


def test_modal_correlation_all_scatter_returns_only_matched_rows(monkeypatch):
    monkeypatch.setattr(
        fem_correlation_service,
        "match_modal_modes",
        lambda project_id, **kwargs: {
            "project_id": int(project_id),
            "method": kwargs["method"],
            "mac_threshold": 70.0,
            "max_freq_error_ratio": kwargs["max_freq_error_ratio"],
            "rows": [
                {
                    "status": "matched",
                    "recommended": True,
                    "fem_mode_no": 1,
                    "test_mode_no": 1,
                    "dof_pair_count": 8,
                    "mac": 97.5,
                    "freq_fem": 31.8,
                    "freq_test": 32.5,
                    "freq_error_ratio": -0.0215,
                    "flip": False,
                },
                {
                    "status": "matched",
                    "recommended": True,
                    "fem_mode_no": 2,
                    "test_mode_no": 2,
                    "dof_pair_count": 8,
                    "mac": 95.0,
                    "freq_fem": 64.2,
                    "freq_test": 63.9,
                    "freq_error_ratio": 0.0047,
                    "flip": True,
                },
            ],
            "summary": {"matched_pair_count": 2},
            "unmatched_fem_modes": [3],
            "unmatched_test_modes": [4],
        },
    )

    payload = fem_correlation_service.get_modal_correlation_all_scatter_payload(
        18,
        mac_threshold=0.7,
        max_freq_error_ratio=0.2,
        method="greedy",
    )

    assert payload["data"][0]["label"] == "matched_modes"
    assert payload["data"][0]["data"] == [["31.800", 32.5], ["64.200", 63.9]]
    assert payload["summary"]["matched_pair_count"] == 2
    assert payload["unmatched_fem_modes"] == [3]
