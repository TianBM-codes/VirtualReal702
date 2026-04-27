from pathlib import Path

from src.l3.core.errors import NotFoundError, ValidationError
from tools.inp_tree import parse_inp, print_tree


def get_inp_tree(file_path: str, show_labels: bool = True, max_labels: int = 8) -> dict:
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise NotFoundError(f"未找到 inp 文件: {path}", {"file_path": str(path)})
    if not path.is_file():
        raise ValidationError("file_path 必须是文件", {"file_path": str(path)})
    if int(max_labels) <= 0:
        raise ValidationError("max_labels 必须大于 0", {"max_labels": max_labels})
    model = parse_inp(str(path))
    return print_tree(model, show_labels=show_labels, max_labels=int(max_labels))
