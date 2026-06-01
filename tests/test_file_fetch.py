from pathlib import Path

from src.utils.file_fetch import materialize_source_file


def test_materialize_source_file_copies_local_file_into_dest_dir(tmp_path: Path):
    source = tmp_path / "door.inp"
    source.write_text("*Heading\n", encoding="utf-8")
    dest_dir = tmp_path / "workspace"

    copied = materialize_source_file(str(source), str(dest_dir))

    copied_path = Path(copied)
    assert copied_path == dest_dir / "door.inp"
    assert copied_path.read_text(encoding="utf-8") == "*Heading\n"
    assert source.read_text(encoding="utf-8") == "*Heading\n"


def test_materialize_source_file_returns_same_path_when_already_in_dest_dir(tmp_path: Path):
    dest_dir = tmp_path / "workspace"
    dest_dir.mkdir(parents=True, exist_ok=True)
    source = dest_dir / "case.odb"
    source.write_bytes(b"odb")

    copied = materialize_source_file(str(source), str(dest_dir))

    assert Path(copied) == source.resolve()
