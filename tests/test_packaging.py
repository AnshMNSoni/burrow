import os
import re
import json
import zipfile
import subprocess
import tempfile
from pathlib import Path
import pytest

def test_version_alignment():
    root = Path(__file__).parent.parent.resolve()
    
    # 1. Read vscode/package.json version
    package_json_path = root / "vscode" / "package.json"
    assert package_json_path.exists()
    with open(package_json_path, "r", encoding="utf-8") as f:
        pkg_data = json.load(f)
    vscode_version = pkg_data.get("version")
    assert vscode_version, "vscode/package.json version is missing"

    # 2. Read pyproject.toml version
    pyproject_path = root / "pyproject.toml"
    assert pyproject_path.exists()
    pyproject_content = pyproject_path.read_text(encoding="utf-8")
    pyproject_match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', pyproject_content, re.MULTILINE)
    assert pyproject_match, "pyproject.toml version is missing"
    pyproject_version = pyproject_match.group(1)

    # 3. Read src/burrow/__init__.py __version__
    init_path = root / "src" / "burrow" / "__init__.py"
    assert init_path.exists()
    init_content = init_path.read_text(encoding="utf-8")
    init_match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_content, re.MULTILINE)
    assert init_match, "__version__ in src/burrow/__init__.py is missing"
    init_version = init_match.group(1)

    # 4. Read src/burrow/api/app.py version
    app_path = root / "src" / "burrow" / "api" / "app.py"
    assert app_path.exists()
    app_content = app_path.read_text(encoding="utf-8")
    app_match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', app_content)
    assert app_match, "FastAPI version in src/burrow/api/app.py is missing"
    app_version = app_match.group(1)

    # Assert they are all aligned
    assert vscode_version == pyproject_version == init_version == app_version, (
        f"Versions are not aligned!\n"
        f"vscode/package.json: {vscode_version}\n"
        f"pyproject.toml: {pyproject_version}\n"
        f"src/burrow/__init__.py: {init_version}\n"
        f"src/burrow/api/app.py: {app_version}"
    )

def test_vsix_packaging():
    root = Path(__file__).parent.parent.resolve()
    vscode_dir = root / "vscode"
    
    # Verify npm is installed
    try:
        subprocess.run(["npm", "--version"], capture_output=True, check=True, shell=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        pytest.skip("npm is not installed or not available in PATH")

    # Clean old vsix packages in vscode/ to avoid conflicts
    for f in vscode_dir.glob("*.vsix"):
        try:
            f.unlink()
        except OSError:
            pass

    # We package into a temporary directory to keep the workspace clean
    with tempfile.TemporaryDirectory() as tmpdir:
        vsix_output_path = Path(tmpdir) / "burrow-test.vsix"
        
        # Compile TypeScript first
        compile_res = subprocess.run(
            ["npm", "run", "compile"],
            cwd=str(vscode_dir),
            capture_output=True,
            text=True,
            shell=True
        )
        assert compile_res.returncode == 0, f"TypeScript compilation failed:\nStdout: {compile_res.stdout}\nStderr: {compile_res.stderr}"

        # Package the extension using local @vscode/vsce
        pack_res = subprocess.run(
            ["npx", "@vscode/vsce", "package", "-o", str(vsix_output_path)],
            cwd=str(vscode_dir),
            capture_output=True,
            text=True,
            shell=True
        )
        assert pack_res.returncode == 0, f"vsce packaging failed:\nStdout: {pack_res.stdout}\nStderr: {pack_res.stderr}"
        
        assert vsix_output_path.exists(), "The VSIX package was not generated"
        assert vsix_output_path.stat().st_size > 0, "The generated VSIX is empty"

        # Inspect VSIX ZIP contents
        with zipfile.ZipFile(vsix_output_path, "r") as z:
            namelist = z.namelist()
            
            # VSCode vsce packages everything inside 'extension/' directory in the ZIP
            assert "extension/package.json" in namelist, "package.json is missing from packaged extension"
            assert "extension/out/extension.js" in namelist, "extension.js output file is missing from packaged extension"
            assert "extension/README.md" in namelist, "README.md is missing from packaged extension"
            assert "extension/LICENSE.txt" in namelist, "LICENSE is missing from packaged extension"
            
            # Verify ignored files
            src_files = [name for name in namelist if name.startswith("extension/src/")]
            assert len(src_files) == 0, f"TypeScript source files packaged but should be ignored: {src_files}"
            
            assert "extension/tsconfig.json" not in namelist, "tsconfig.json packaged but should be ignored"
            assert "extension/package-lock.json" not in namelist, "package-lock.json packaged but should be ignored"
