"""Import the app with the extra source path previously added by PyCharm."""
import subprocess
import sys
from pathlib import Path


def test_app_import_with_l3_source_root():
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", "\n".join([
            "import sys",
            "from pathlib import Path",
            "sys.path.insert(1, str(Path('src/l3').resolve()))",
            "import services",
            "assert Path(services.__file__).resolve() == Path('services/__init__.py').resolve()",
            "import services.model_update",
            "import app",
            "assert app.app is not None",
        ])],
        cwd=project, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
