"""Shared scope and TXT metadata selection for measured load-level data."""


def scope_ids(work_condition_id=None, measurement_id=None):
    result = []
    for value in (work_condition_id, measurement_id):
        if value is not None and (not isinstance(value, str) or len(value) > 100):
            raise ValueError("Load-level scope IDs must be strings of at most 100 characters")
        result.append(value or "")
    return tuple(result)


def import_metadata(extra, work_condition_id=None, measurement_id=None):
    condition, measurement = scope_ids(work_condition_id, measurement_id)
    if not condition and not measurement:
        return extra.get("test_load_level_import", {})
    return extra.get("test_load_level_imports", {}).get(condition, {}).get(measurement, {})
