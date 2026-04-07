from pathlib import Path

from src.inp import parse_inp
from tools.inp_tree import print_tree


def test_print_tree_shows_initial_boundary_conditions(tmp_path: Path, capsys):
    inp_path = tmp_path / "tree_boundary.inp"
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
*Nset, nset=FIXED
1,2
*Boundary
FIXED, ENCASTRE
*Step
*Static
*Boundary, op=NEW
FIXED, 1, 2, 0.0
*End Step
""",
        encoding="utf-8",
    )

    model = parse_inp(str(inp_path))
    print_tree(model, show_labels=False, max_labels=3)
    output = capsys.readouterr().out

    assert "Initial Boundary Conditions" in output
    assert "FIXED  DOF 1-6 = 0.0" in output
    assert "Boundary Conditions (1)" in output
