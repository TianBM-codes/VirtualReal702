import io
import zipfile

from tools.export_source import scan_archive


def test_delivery_scan_checks_paths_text_and_wide_character_assets():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("FORBIDDEN/module.py", "pass")
        archive.writestr("notes.md", "a FoRbIdDeN reference")
        archive.writestr("asset.bin", "FORBIDDEN".encode("utf-16-le"))
        archive.writestr("good.py", "pass")
    assert scan_archive(buffer.getvalue(), ["forbidden"]) == [
        "FORBIDDEN/module.py (path)", "notes.md (content)", "asset.bin (content)"
    ]
