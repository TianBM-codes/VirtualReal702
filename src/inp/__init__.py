"""
INP Parser — public API.

Usage:
    from src.inp import parse_inp

    model = parse_inp("/path/to/model.inp")

    for part_name, part in model.parts.items():
        print(f"{part_name}: {len(part.nodes)} nodes, {len(part.elements)} elements")

    if model.assembly:
        for inst_name, inst in model.assembly.instances.items():
            print(f"  Instance {inst_name} → Part {inst.part_name}")
            print(f"  Transform matrix:\\n{inst.transform_matrix}")

    for d in model.diagnostics:
        print(d)
"""
from .parser   import InpParser
from .resolver import resolve
from .model    import (
    InpModel, Part, Assembly, Instance, Element, Node,
    Nset, Elset, Surface, Section, Material, Amplitude,
    StepDeclaration, ABAQUS_TO_FACTORY,
)
from .diagnostics import Diagnostic


def parse_inp(filepath: str, resolve_refs: bool = True) -> InpModel:
    """
    Parse an Abaqus INP file and return a fully-resolved InpModel.

    Args:
        filepath:     Path to the .inp file.
        resolve_refs: If True (default), run the Resolver to expand set
                      nesting and compute instance transform matrices.
                      Set to False to get the raw Parser output for debugging.

    Returns:
        InpModel with diagnostics attached.
        Check model.diagnostics for warnings/errors.
    """
    parser = InpParser()
    model  = parser.parse(filepath)
    if resolve_refs:
        resolve(model)
    return model


__all__ = [
    "parse_inp",
    "InpModel", "Part", "Assembly", "Instance", "Element", "Node",
    "Nset", "Elset", "Surface", "Section", "Material", "Amplitude",
    "StepDeclaration", "Diagnostic", "ABAQUS_TO_FACTORY",
]
