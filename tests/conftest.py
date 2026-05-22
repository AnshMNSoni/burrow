import pytest
import tempfile
from pathlib import Path

@pytest.fixture
def temp_project_root():
    """Creates a temporary folder mimicking a project root with source files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root_path = Path(tmpdir)
        
        # Create a mock source python file
        py_file = root_path / "app.py"
        py_file.write_text(
            "def foo():\n"
            "    print('hello')\n"
            "    x = 1 / 0\n"
            "    return x\n",
            encoding="utf-8"
        )
        
        # Create a mock JS source file
        js_file = root_path / "index.js"
        js_file.write_text(
            "function test() {\n"
            "    let obj = {};\n"
            "    console.log(obj.nonexistent.value);\n"
            "}\n",
            encoding="utf-8"
        )
        
        yield root_path
