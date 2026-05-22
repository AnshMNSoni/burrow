import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from burrow.symbol.extractor import extract_symbols, PythonWalker, JavaScriptWalker
from burrow.symbol.graph import SymbolGraphBuilder
from burrow.symbol.analyzer import SymbolGraphAnalyzer
from burrow.core.engine import BurrowEngine
from burrow.cli.main import main

def test_python_ast_extraction(temp_project_root):
    # Create a Python file with rich constructs
    py_code = (
        "import math\n"
        "from os import path as ospath\n"
        "from .sibling import util_func\n"
        "\n"
        "class MyClass:\n"
        "    \"\"\"This is a test class.\"\"\"\n"
        "    def method_a(self, x):\n"
        "        y = x + 1\n"
        "        math.sin(y)\n"
        "        util_func(y)\n"
        "        # Undefined reference usage\n"
        "        return y + undefined_var\n"
    )
    py_file = temp_project_root / "py_test.py"
    py_file.write_text(py_code, encoding="utf-8")

    info = extract_symbols(py_file, temp_project_root)
    assert info is not None
    assert info.file_path == "py_test.py"
    
    # 1. Verify Symbols
    symbols = {s.name: s for s in info.symbols}
    assert "MyClass" in symbols
    assert symbols["MyClass"].type == "CLASS"
    assert "This is a test class." in symbols["MyClass"].docstring
    
    assert "method_a" in symbols or "MyClass.method_a" in symbols
    method_sym = symbols.get("method_a") or symbols.get("MyClass.method_a")
    assert method_sym is not None
    assert method_sym.type == "METHOD"
    
    # 2. Verify Imports
    imports = {i.local_name: i for i in info.imports}
    assert "math" in imports
    assert imports["math"].module_path == "math"
    assert "ospath" in imports
    assert imports["ospath"].module_path == "os"
    assert imports["ospath"].imported_name == "path"
    assert "util_func" in imports
    assert imports["util_func"].module_path == ".sibling"
    assert imports["util_func"].is_relative is True

    # 3. Verify Local Defs & References
    scope_name = "MyClass.method_a" if "MyClass.method_a" in info.local_defs else "method_a"
    assert "x" in info.local_defs[scope_name]
    assert "y" in info.local_defs[scope_name]
    
    refs = {r[0] for r in info.references if r[2] == scope_name}
    assert "math" in refs
    assert "util_func" in refs
    assert "undefined_var" in refs


def test_js_ts_ast_extraction(temp_project_root):
    # Create JS/TS files
    js_code = (
        "import defaultExp, { foo, bar as b } from './module';\n"
        "const express = require('express');\n"
        "const { destructure } = require('./utils');\n"
        "\n"
        "/** Helper class */\n"
        "class Helper {\n"
        "    doSomething(a) {\n"
        "        const result = a * 2;\n"
        "        foo(result);\n"
        "        // Loose comparison\n"
        "        if (result == 4) {\n"
        "            return result;\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
    js_file = temp_project_root / "js_test.js"
    js_file.write_text(js_code, encoding="utf-8")

    info = extract_symbols(js_file, temp_project_root)
    assert info is not None
    assert info.file_path == "js_test.js"

    # Verify Symbols
    symbols = {s.name: s for s in info.symbols}
    assert "Helper" in symbols
    assert symbols["Helper"].type == "CLASS"
    assert "Helper class" in symbols["Helper"].docstring

    # Verify Imports
    imports = {i.local_name: i for i in info.imports}
    assert "defaultExp" in imports
    assert "foo" in imports
    assert "b" in imports
    assert imports["b"].imported_name == "bar"
    assert "express" in imports
    assert "destructure" in imports

    # Verify Calls
    calls = {c.callee for c in info.calls}
    assert "foo" in calls


def test_cross_file_resolution_and_cycles(temp_project_root):
    # File A: imports B and calls a function
    code_a = (
        "from .file_b import get_val\n"
        "def run():\n"
        "    return get_val()\n"
    )
    # File B: imports A (cycle!) and defines function
    code_b = (
        "from .file_a import run\n"
        "def get_val():\n"
        "    return 42\n"
    )
    (temp_project_root / "file_a.py").write_text(code_a, encoding="utf-8")
    (temp_project_root / "file_b.py").write_text(code_b, encoding="utf-8")

    builder = SymbolGraphBuilder(temp_project_root)
    g = builder.build()
    
    # 1. Verify Import Edges
    assert g.has_edge("file:file_a.py", "file:file_b.py")
    assert g.has_edge("file:file_b.py", "file:file_a.py")

    # 2. Verify Resolved Call Edge
    # run() calls get_val() in file_b.py
    caller_node = "symbol:file_a.py:run"
    callee_node = "symbol:file_b.py:get_val"
    assert g.has_edge(caller_node, callee_node)

    # 3. Verify Circular Dependency Detection
    analyzer = SymbolGraphAnalyzer(temp_project_root, builder)
    smells = analyzer.analyze()
    
    cycles = [s for s in smells if s.smell_type == "circular_dependency"]
    assert len(cycles) > 0
    assert "file_a.py" in cycles[0].message
    assert "file_b.py" in cycles[0].message
    assert cycles[0].severity == "error"


def test_vulnerabilities_detection(temp_project_root):
    code = (
        "def unsafe_func(val, arr, idx):\n"
        "    # 1. Null dereference (unguarded)\n"
        "    print(val.attribute)\n"
        "\n"
        "    # 2. Null dereference (guarded - should not trigger warning)\n"
        "    if val is not None:\n"
        "        print(val.attribute)\n"
        "\n"
        "    # 3. Division by zero (unguarded)\n"
        "    res = 100 / idx\n"
        "\n"
        "    # 4. Division by zero (guarded)\n"
        "    if idx != 0:\n"
        "        res2 = 100 / idx\n"
        "\n"
        "    # 5. Array index (unguarded)\n"
        "    item = arr[idx]\n"
        "\n"
        "    # 6. Array index (guarded)\n"
        "    if idx < len(arr):\n"
        "        item2 = arr[idx]\n"
    )
    (temp_project_root / "vuln_test.py").write_text(code, encoding="utf-8")

    builder = SymbolGraphBuilder(temp_project_root)
    builder.build()
    analyzer = SymbolGraphAnalyzer(temp_project_root, builder)
    smells = analyzer.analyze()
    smells = [s for s in smells if s.file_path == "vuln_test.py"]

    # Verify smells
    smell_types = [s.smell_type for s in smells]
    assert "null_dereference" in smell_types
    assert "missing_guard" in smell_types

    # Let's count them:
    # 1 null dereference warning (line 3, unsafe)
    null_smells = [s for s in smells if s.smell_type == "null_dereference"]
    assert len(null_smells) == 1
    assert null_smells[0].line_number == 3

    # 1 missing guard division warning (line 10)
    # 1 missing guard subscript warning (line 17)
    guard_smells = [s for s in smells if s.smell_type == "missing_guard"]
    assert len(guard_smells) == 2
    assert {s.line_number for s in guard_smells} == {10, 17}


def test_broken_reference_detection(temp_project_root):
    code = (
        "def func_a(x):\n"
        "    y = x + 1\n"
        "    # undefined_var is undefined\n"
        "    return y + undefined_var\n"
    )
    (temp_project_root / "broken_ref.py").write_text(code, encoding="utf-8")

    builder = SymbolGraphBuilder(temp_project_root)
    builder.build()
    analyzer = SymbolGraphAnalyzer(temp_project_root, builder)
    smells = analyzer.analyze()

    broken_refs = [s for s in smells if s.smell_type == "broken_reference"]
    assert len(broken_refs) == 1
    assert broken_refs[0].line_number == 4
    assert "undefined_var" in broken_refs[0].message
    assert broken_refs[0].severity == "error"


def test_cli_check_command_clean(temp_project_root):
    # Clean codebase -> no smells
    with patch("sys.argv", ["burrow", "-r", str(temp_project_root), "check"]):
        with patch("sys.exit") as mock_exit:
            main()
            # If no errors/smells, should exit with 0 (clean setup might have warnings if temp files have unguarded codes)
            # Conftest has: "let obj = {}; console.log(obj.nonexistent.value);" -> which has null dereference warning but not error!
            # So has_error will be False, meaning exit code is 0!
            mock_exit.assert_called_once_with(0)


def test_cli_check_command_failure(temp_project_root):
    # Add a high-severity error (broken reference)
    code = (
        "def error_func():\n"
        "    return undefined_var_reference\n"
    )
    (temp_project_root / "bad_ref.py").write_text(code, encoding="utf-8")

    with patch("sys.argv", ["burrow", "-r", str(temp_project_root), "check"]):
        with patch("sys.exit") as mock_exit:
            main()
            mock_exit.assert_called_once_with(1)
