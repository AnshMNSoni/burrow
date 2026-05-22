import json
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch, MagicMock
import pytest

from burrow.workspace.scanner import WorkspaceScanner
from burrow.workspace.models import WorkspaceContext
from burrow.core.engine import BurrowEngine
from burrow.cli.main import main


def test_framework_and_dependency_detection(temp_project_root):
    # 1. Create requirements.txt
    reqs_file = temp_project_root / "requirements.txt"
    reqs_file.write_text("fastapi==0.100.0\nuvicorn>=0.20.0\n# comment\n", encoding="utf-8")

    # 2. Create package.json
    package_file = temp_project_root / "package.json"
    package_file.write_text(
        json.dumps({
            "dependencies": {
                "react": "^18.2.0",
                "express": "^4.18.2"
            },
            "devDependencies": {
                "typescript": "^5.0.0"
            }
        }),
        encoding="utf-8"
    )

    # 3. Create pyproject.toml
    pyproject_file = temp_project_root / "pyproject.toml"
    pyproject_file.write_text(
        "[project]\n"
        "dependencies = [\n"
        "    \"pydantic>=2.0.0\"\n"
        "]\n"
        "[tool.poetry.dependencies]\n"
        "django = \"^4.0.0\"\n",
        encoding="utf-8"
    )

    scanner = WorkspaceScanner(temp_project_root)
    structure, dependencies = scanner._scan_structure_and_dependencies()

    # Frameworks detected from parsing configs
    assert "fastapi" in structure.detected_frameworks
    assert "django" in structure.detected_frameworks
    assert "react" in structure.detected_frameworks
    assert "express" in structure.detected_frameworks

    # Package managers detected
    assert "pip" in structure.package_managers
    assert "npm/yarn/pnpm" in structure.package_managers
    assert "pip/poetry" in structure.package_managers

    # Check parsed dependencies
    assert "requirements.txt" in dependencies
    req_deps = {d.name: d.version for d in dependencies["requirements.txt"]}
    assert req_deps["fastapi"] == "0.100.0"
    assert req_deps["uvicorn"] == "0.20.0"

    assert "package.json" in dependencies
    pkg_deps = {d.name: (d.version, d.scope) for d in dependencies["package.json"]}
    assert pkg_deps["react"] == ("^18.2.0", "production")
    assert pkg_deps["typescript"] == ("^5.0.0", "dev")

    assert "pyproject.toml" in dependencies
    pyproj_deps = {d.name: d.version for d in dependencies["pyproject.toml"]}
    assert pyproj_deps["pydantic"] == "2.0.0"
    assert pyproj_deps["django"] == "^4.0.0"


def test_git_context_extraction(temp_project_root):
    # Setup mock git repository directory
    git_dir = temp_project_root / ".git"
    git_dir.mkdir(exist_ok=True)

    scanner = WorkspaceScanner(temp_project_root)

    # Mock subprocess.run
    def mock_run(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        if "rev-parse" in cmd_str:
            return CompletedProcess(cmd, 0, stdout="feature-branch\n", stderr="")
        elif "status --porcelain" in cmd_str:
            stdout = " M app.py\n?? new_module.py\n D deleted.py\n"
            return CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        elif "log" in cmd_str:
            stdout = "app.py\nindex.js\n"
            return CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        elif "diff" in cmd_str:
            stdout = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
            return CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=mock_run):
        git_context = scanner._extract_git_context()

    assert git_context is not None
    assert git_context.active_branch == "feature-branch"
    
    recent_paths = {change.file_path: change.status for change in git_context.recent_changes}
    assert recent_paths["app.py"] == "modified"
    assert recent_paths["new_module.py"] == "untracked"
    assert recent_paths["deleted.py"] == "deleted"
    assert recent_paths["index.js"] == "committed"

    assert "diff --git a/app.py" in git_context.current_diff


def test_tree_sitter_import_parsing(temp_project_root):
    # 1. Create a Python file with imports
    py_content = (
        "import sys\n"
        "from os import path\n"
        "import requests as req\n"
        "from .utils import helper\n"
        "from ..core.engine import BurrowEngine\n"
    )
    (temp_project_root / "test_py.py").write_text(py_content, encoding="utf-8")

    # 2. Create a JS file with imports
    js_content = (
        "import react from 'react';\n"
        "const express = require('express');\n"
        "const local = require('./local_helper');\n"
    )
    (temp_project_root / "test_js.js").write_text(js_content, encoding="utf-8")

    # 3. Create a TSX file with imports
    tsx_content = (
        "import { useState } from 'react';\n"
        "import * as next from 'next/router';\n"
    )
    (temp_project_root / "test_tsx.tsx").write_text(tsx_content, encoding="utf-8")

    scanner = WorkspaceScanner(temp_project_root)
    structure, dependencies = scanner._scan_structure_and_dependencies()
    import_map = scanner._map_imports(structure)

    # Validate Python imports
    py_relations = [r for r in import_map if r.source_file == "test_py.py"]
    targets = {r.target_module: r.is_relative for r in py_relations}
    assert "sys" in targets
    assert "os" in targets
    assert "requests" in targets
    assert "utils" in targets
    assert targets["utils"] is True
    assert "core.engine" in targets
    assert targets["core.engine"] is True

    # Validate JS imports
    js_relations = [r for r in import_map if r.source_file == "test_js.js"]
    js_targets = {r.target_module: r.is_relative for r in js_relations}
    assert "react" in js_targets
    assert js_targets["react"] is False
    assert "express" in js_targets
    assert js_targets["express"] is False
    assert "./local_helper" in js_targets
    assert js_targets["./local_helper"] is True

    # Validate TSX imports
    tsx_relations = [r for r in import_map if r.source_file == "test_tsx.tsx"]
    tsx_targets = {r.target_module: r.is_relative for r in tsx_relations}
    assert "react" in tsx_targets
    assert "next/router" in tsx_targets

    # Check framework inference
    inferred = scanner._infer_frameworks_from_imports(import_map)
    assert "react" in inferred
    assert "express" in inferred
    assert "next.js" in inferred


def test_core_engine_correlation(temp_project_root):
    # Setup mock git status
    git_dir = temp_project_root / ".git"
    git_dir.mkdir(exist_ok=True)

    # We want app.py to be reported as modified in Git
    def mock_run(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        if "rev-parse" in cmd_str:
            return CompletedProcess(cmd, 0, stdout="main\n", stderr="")
        elif "status --porcelain" in cmd_str:
            # Report app.py as modified
            return CompletedProcess(cmd, 0, stdout=" M app.py\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    trace = (
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 3, in foo\n"
        "    x = 1 / 0\n"
        "ZeroDivisionError: division by zero\n"
    )

    engine = BurrowEngine(project_root=temp_project_root)
    with patch("subprocess.run", side_effect=mock_run):
        result = engine.analyze_content(trace)

    # The frame pointing to app.py should be annotated
    assert result.error.frames[0].metadata.get("git_status") == "modified"
    assert result.workspace_context is not None
    assert result.workspace_context.git.active_branch == "main"


def test_cli_scan_subcommand(temp_project_root):
    # Invoke scan subcommand
    with patch("sys.argv", ["burrow", "-r", str(temp_project_root), "scan"]):
        with patch("sys.stdout.write") as mock_stdout_write:
            # We also patch uvicorn run to avoid starting server or anything, but scan shouldn't call it anyway.
            # Capture print outputs
            print_calls = []
            def mock_print(*args, **kwargs):
                print_calls.append(" ".join(map(str, args)))

            with patch("builtins.print", side_effect=mock_print):
                main()

    # The printed output should be valid JSON
    assert len(print_calls) > 0
    json_str = print_calls[0]
    parsed_json = json.loads(json_str)

    assert "structure" in parsed_json
    assert "dependencies" in parsed_json
    assert "import_map" in parsed_json
