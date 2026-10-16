"""Check the built distribution includes assets, schema, type marker, and CLI."""

import configparser
import tempfile
import zipfile
from pathlib import Path



def main():
    wheels = list(Path("dist").glob("*.whl"))
    assert len(wheels) == 1, "Build exactly one wheel first"
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        for filename in ["index.html", "app.js", "graph.js", "styles.css"]:
            assert f"laranex_schema_viewer/static/{filename}" in names
        assert "laranex_schema_viewer/schema.json" in names
        assert "laranex_schema_viewer/py.typed" in names
        entries = next(n for n in names if n.endswith("entry_points.txt"))
        parser = configparser.ConfigParser()
        parser.read_string(wheel.read(entries).decode())
        assert parser["console_scripts"]["laranex-schema-viewer"] == "laranex_schema_viewer.cli:main"
        assert not any("reference.json" in n or "tests/" in n or ".validation/" in n for n in names)
        with tempfile.TemporaryDirectory() as directory:
            wheel.extractall(directory)
            # Import from the extracted wheel in a clean child, without the source tree.
            import os
            import subprocess
            import sys

            result = subprocess.run([sys.executable, "-c", (
                "from importlib.resources import files; "
                "import laranex_schema_viewer; "
                "assert files('laranex_schema_viewer').joinpath('static/index.html').read_text(); "
                "from laranex_schema_viewer.cli import main; main(['--version'])"
            )], cwd=directory, env=dict(os.environ, PYTHONPATH=directory,
                                       PYTHONDONTWRITEBYTECODE="1"),
                capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, result.stderr
    print("Wheel assets, schema, CLI entry point, and isolated import: passed")


if __name__ == "__main__":
    main()

