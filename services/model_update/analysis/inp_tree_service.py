from pathlib import Path

from src.l3.core.errors import NotFoundError, ValidationError
from tools.inp_tree import parse_inp_tree


def get_inp_tree(file_path: str, show_labels: bool = True, max_labels: int = 8) -> dict:
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise NotFoundError(f"inp file not found: {path}", {"file_path": str(path)})
    if not path.is_file():
        raise ValidationError("file_path must be a file", {"file_path": str(path)})
    if int(max_labels) <= 0:
        raise ValidationError("max_labels must be > 0", {"max_labels": max_labels})
    return parse_inp_tree(str(path), show_labels=show_labels, max_labels=int(max_labels))
