from pathlib import Path

from services.model_update.analysis.inp_tree_service import get_inp_tree


def test_get_inp_tree_returns_structured_parse_result(tmp_path: Path):
    inp_path = tmp_path / "tree_model.inp"
    inp_path.write_text(
        """*Heading
*Part, name=P1
*Node
1, 0., 0., 0.
2, 1., 0., 0.
3, 1., 1., 0.
4, 0., 1., 0.
*Element, type=S4, elset=PANEL
1, 1, 2, 3, 4
*Nset, nset=NALL
1,2,3,4
*Elset, elset=EALL
1
*Shell Section, elset=PANEL, material=STEEL
2.5, 5
*Material, name=STEEL
*Elastic
210000., 0.3
*Step
*Static
0.1, 1.0
*End Step
""",
        encoding="utf-8",
    )

    result = get_inp_tree(str(inp_path), show_labels=True, max_labels=3)

    assert result["file_path"] == str(inp_path.resolve())
    assert result["tree"]["summary"]["part_count"] == 1
    assert result["tree"]["summary"]["material_count"] == 1
    assert result["tree"]["summary"]["step_count"] == 1
    assert result["tree"]["parts"][0]["name"] == "P1"
    assert result["tree"]["parts"][0]["nodes"]["count"] == 4
    assert result["tree"]["parts"][0]["nsets"][0]["name"] == "NALL"
    assert result["tree"]["materials"][0]["name"] == "STEEL"
    assert result["tree"]["steps"][0]["step_type"] == "STATIC"
