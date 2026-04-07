from pathlib import Path

from src.inp import parse_inp
from tools.inp_tree import print_tree


def test_print_tree_shows_conductivity_and_expansion_values(tmp_path: Path, capsys):
    inp_path = tmp_path / "tree_materials.inp"
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
*Shell Section, elset=PANEL, material=STEEL
2.5, 5
*Material, name=STEEL
*Density
7850.
*Elastic
210000., 0.3
*Conductivity
45.6
*Expansion
1.23e-05
*Step
*Static
0.1, 1.0
*End Step
""",
        encoding="utf-8",
    )

    model = parse_inp(str(inp_path))
    print_tree(model, show_labels=False, max_labels=3)
    output = capsys.readouterr().out

    assert "Density(7.85e+03)" in output
    assert "Elastic/ISOTROPIC(E=2.1e+05)" in output
    assert "Conductivity(k=45.6)" in output
    assert "Expansion(alpha=1.23e-05)" in output
