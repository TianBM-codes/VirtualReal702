import os
from pathlib import Path

from src.utils.file_fetch import materialize_source_file, resolve_runner_source


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


def test_resolve_runner_source_ascii_local_returns_path_unchanged(tmp_path: Path):
    source = tmp_path / "model.odb"
    source.write_bytes(b"odb")
    workspace = tmp_path / "ws"

    resolved = resolve_runner_source(str(source), str(workspace))

    # ASCII 本地文件：原样返回，不复制、不在 workspace 落地任何东西。
    assert resolved == os.path.abspath(str(source))
    assert not workspace.exists() or not any(workspace.iterdir())


def test_resolve_runner_source_non_ascii_local_hardlinks_without_copy(tmp_path: Path):
    source = tmp_path / "车门模型.odb"
    source.write_bytes(b"odb-bytes")
    workspace = tmp_path / "ws"

    resolved = resolve_runner_source(str(source), str(workspace), "project_odb_a1b2c3d4.odb")

    resolved_path = Path(resolved)
    # 暴露出的是 ASCII 路径，位于 workspace 内，名字用传入的 ASCII 别名。
    resolved.encode("ascii")  # 不抛异常 = 纯 ASCII
    assert resolved_path.parent == workspace
    assert resolved_path.name == "project_odb_a1b2c3d4.odb"
    # 原文件原样保留。
    assert source.exists()
    # 硬链接 = 同一份数据（同 inode / 同内容），零复制。
    assert resolved_path.read_bytes() == b"odb-bytes"
    if hasattr(os, "samefile"):
        assert os.path.samefile(str(source), resolved)


def test_resolve_runner_source_non_ascii_local_is_idempotent(tmp_path: Path):
    source = tmp_path / "结果工况.odb"
    source.write_bytes(b"odb")
    workspace = tmp_path / "ws"

    first = resolve_runner_source(str(source), str(workspace), "result_case01_deadbeef.odb")
    second = resolve_runner_source(str(source), str(workspace), "result_case01_deadbeef.odb")

    # 重复调用（重试）复用同一个硬链接，不报错、不重复复制。
    assert first == second
    assert source.exists()
