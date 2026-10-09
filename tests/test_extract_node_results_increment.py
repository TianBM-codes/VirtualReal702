"""Increment sorting must follow solver increments rather than frame numbers."""

from tools.extract_node_results import increment, result_order


def row(frame, description, step_index=0):
    return {
        "step_index": str(step_index), "step": "Step-{}".format(step_index + 1),
        "frame_idx": str(frame), "frame_desc": description,
        "instance": "PART-1", "node": "461", "section_point": "",
        "field": "S", "component": "S11",
    }


def test_increment_sort_is_numeric_and_independent_of_frame_index():
    values = [row(0, "Increment 10: Step Time = 1"),
              row(1, "Increment 2: Step Time = .2")]
    assert [increment(value) for value in sorted(values, key=result_order)] == [2, 10]


def test_explicit_increment_precedes_description_and_frame_fallback():
    value = row(1, "Increment 10: Step Time = 1")
    value["increment"] = "5"
    assert increment(value) == 5
    assert increment(row(7, "Mode 1")) == 7


def test_steps_are_separate_when_increment_restarts():
    values = [row(0, "Increment 0", step_index=1), row(9, "Increment 9")]
    assert [int(value["step_index"]) for value in sorted(values, key=result_order)] == [0, 1]
