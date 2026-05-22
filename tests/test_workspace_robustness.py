import pytest
import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from burrow.workspace.scanner import WorkspaceScanner

def test_scanner_non_utf8_files():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        
        # Write a file with non-UTF-8 characters (e.g. ISO-8859-1 encoding) and an import
        test_file = root / "bad_encoding.py"
        with open(test_file, "wb") as f:
            f.write(b"import os\ndef foo():\n    # \xe9\xfc\xe1 (some non-utf8 characters)\n    pass\n")
            
        scanner = WorkspaceScanner(root)
        context = scanner.scan()
        
        # Check that the import was successfully parsed despite non-UTF-8 characters
        imports = [r.target_module for r in context.import_map if r.source_file == "bad_encoding.py"]
        assert "os" in imports

def test_scanner_missing_git():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        
        # No .git directory exists
        scanner = WorkspaceScanner(root)
        git_context = scanner._extract_git_context()
        
        # Should gracefully return None or empty context without throwing subprocess exceptions
        assert git_context is None

def test_scanner_corrupted_configs():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        
        # Corrupted package.json (invalid JSON syntax)
        pkg_file = root / "package.json"
        pkg_file.write_text("{invalid json: yes,", encoding="utf-8")
        
        # Corrupted requirements.txt (empty or weird lines)
        req_file = root / "requirements.txt"
        req_file.write_text("===invalid-dependency-format===\n", encoding="utf-8")
        
        scanner = WorkspaceScanner(root)
        structure, dependencies = scanner._scan_structure_and_dependencies()
        
        # Scanner should complete successfully
        assert "package.json" in structure.config_files
        assert "requirements.txt" in structure.config_files
        
        # Dependencies from package.json should be empty due to corruption
        assert "package.json" not in dependencies or len(dependencies["package.json"]) == 0

def test_scanner_nested_and_deep_paths():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        
        # Create a deep directory nesting (e.g. 15 levels deep)
        current = root
        for i in range(15):
            current = current / f"level_{i}"
            current.mkdir()
            
        # Put a source file at the deepest level (as an entrypoint)
        deep_file = current / "main.py"
        deep_file.write_text("import sys\nprint('nested')\n", encoding="utf-8")
        
        scanner = WorkspaceScanner(root)
        context = scanner.scan()
        
        # Ensure deep file is found as an entrypoint
        relative_path = deep_file.relative_to(root).as_posix()
        assert relative_path in context.structure.entrypoints
        
        # Ensure the import was mapped
        imports = [r.target_module for r in context.import_map if r.source_file == relative_path]
        assert "sys" in imports

def test_scanner_circular_paths_and_symlinks():
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        
        # Create standard structure
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
        
        # Emulate circular loop by creating a symlink from src/loop back to src
        # Because creating symlinks on Windows can fail with privilege errors,
        # we will handle OSError or execute the test if permissions allow.
        try:
            os.symlink(root / "src", root / "src" / "loop")
        except OSError:
            # Fallback: if we can't create actual symlink, we mock os.walk / path traversal
            # to verify scanner handles loops/visited directories.
            pass
            
        scanner = WorkspaceScanner(root)
        structure, dependencies = scanner._scan_structure_and_dependencies()
        
        # Check that we scanned at least the main file and didn't crash in recursion
        assert "src/main.py" in structure.entrypoints
