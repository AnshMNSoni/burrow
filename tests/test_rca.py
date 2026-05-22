import pytest
from pathlib import Path
from unittest.mock import patch
from burrow.parser import LogParser
from burrow.rca.engine import RootCauseAnalyzer
from burrow.rca.models import Hypothesis, RCAResult
from burrow.parser.models import NormalizedError, NormalizedFrame
from burrow.workspace.models import WorkspaceContext, GitContext, GitFileStatus
from burrow.symbol.models import SymbolGraphData, CodeSmell
from burrow.cli.main import main

def test_rca_config_issue(temp_project_root):
    # Setup files: .env is missing, .env.example exists
    example_env = temp_project_root / ".env.example"
    example_env.write_text("DATABASE_URL=postgres://localhost:5432/db\nAPI_KEY=secret_key\n", encoding="utf-8")
    
    # We construct a mock traceback error that references API_KEY and DATABASE_URL
    trace = (
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 2, in foo\n"
        "    api_key = os.environ.get(\"API_KEY\")\n"
        "  File \"app.py\", line 3, in bar\n"
        "    db_url = os.environ[\"DATABASE_URL\"]\n"
        "TypeError: 'NoneType' object is not callable\n"
    )
    parser = LogParser()
    error = parser.parse(trace)
    
    # Absolute paths resolution for app.py
    for frame in error.frames:
        frame.file_path = (temp_project_root / "app.py").resolve().as_posix()
        if "API_KEY" in frame.raw_line:
            frame.code_context = 'api_key = os.environ.get("API_KEY")'
        else:
            frame.code_context = 'db_url = os.environ["DATABASE_URL"]'
            
    # Force rebuild crash point and root origin with updated file paths
    error.surfaced_crash_point = error.frames[-1]
    error.root_origin = error.frames[0]
            
    analyzer = RootCauseAnalyzer(temp_project_root)
    result = analyzer.analyze(error)
    
    # Config issue should exist because .env is missing
    config_issues = [h for h in result.hypotheses if h.type == "config_issue"]
    assert len(config_issues) == 1
    assert "missing" in config_issues[0].root_cause.lower()
    assert config_issues[0].confidence_score == 0.85
    
    # Env mismatches should exist because API_KEY and DATABASE_URL are referenced in traceback but missing from .env
    env_mismatches = [h for h in result.hypotheses if h.type == "env_mismatch"]
    assert len(env_mismatches) >= 2
    keys_found = {h.root_cause for h in env_mismatches}
    assert any("API_KEY" in k for k in keys_found)
    assert any("DATABASE_URL" in k for k in keys_found)
    
    # Now create .env with only DATABASE_URL, check that only API_KEY is flagged as env_mismatch
    dot_env = temp_project_root / ".env"
    dot_env.write_text("DATABASE_URL=postgres://localhost:5432/db\n", encoding="utf-8")
    
    result2 = analyzer.analyze(error)
    env_mismatches2 = [h for h in result2.hypotheses if h.type == "env_mismatch"]
    
    # API_KEY should be flagged since it's in the traceback and missing in .env
    assert any("API_KEY" in h.root_cause for h in env_mismatches2)
    # DATABASE_URL is in .env, so it shouldn't be flagged in 1b env_mismatch.
    assert not any("DATABASE_URL" in h.root_cause for h in env_mismatches2 if h.line_number is not None)


def test_rca_recent_changes(temp_project_root):
    trace = (
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 2, in foo\n"
        "    x = 1 / 0\n"
        "ZeroDivisionError: division by zero\n"
    )
    parser = LogParser()
    error = parser.parse(trace)
    
    # Annotate frame metadata with git_status
    for frame in error.frames:
        frame.file_path = (temp_project_root / "app.py").resolve().as_posix()
        frame.metadata["git_status"] = "modified"
        
    error.surfaced_crash_point = error.frames[-1]
    error.root_origin = error.frames[0]
        
    analyzer = RootCauseAnalyzer(temp_project_root)
    result = analyzer.analyze(error)
    
    recent_changes = [h for h in result.hypotheses if h.type == "recent_change"]
    assert len(recent_changes) == 1
    assert recent_changes[0].confidence_score == 0.95  # it's the crash frame
    assert "app.py" in recent_changes[0].origin_file


def test_rca_smells_proximity(temp_project_root):
    trace = (
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 3, in foo\n"
        "    val = my_obj.prop\n"
        "AttributeError: 'NoneType' object has no attribute 'prop'\n"
    )
    parser = LogParser()
    error = parser.parse(trace)
    
    for frame in error.frames:
        frame.file_path = (temp_project_root / "app.py").resolve().as_posix()
        
    error.surfaced_crash_point = error.frames[-1]
    error.root_origin = error.frames[0]
        
    # Setup Symbol Graph with code smells
    smells = [
        CodeSmell(
            smell_type="null_dereference",
            message="Dereference of object 'my_obj' without safety guard",
            file_path="app.py",
            line_number=4, # Close to line 3 (within +/- 3 lines)
            severity="warning"
        )
    ]
    sym_data = SymbolGraphData(smells=smells)
    
    analyzer = RootCauseAnalyzer(temp_project_root)
    result = analyzer.analyze(error, symbol_graph_data=sym_data)
    
    null_refs = [h for h in result.hypotheses if h.type == "null_reference"]
    assert len(null_refs) >= 1
    assert any("my_obj" in h.root_cause for h in null_refs)


def test_rca_signature_matching(temp_project_root):
    trace = (
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 3, in foo\n"
        "    x = y / z\n"
        "ZeroDivisionError: division by zero\n"
    )
    parser = LogParser()
    error = parser.parse(trace)
    for frame in error.frames:
        frame.file_path = (temp_project_root / "app.py").resolve().as_posix()
        
    error.surfaced_crash_point = error.frames[-1]
    error.root_origin = error.frames[0]
        
    analyzer = RootCauseAnalyzer(temp_project_root)
    result = analyzer.analyze(error)
    
    # Division by zero should match error signature
    bad_states = [h for h in result.hypotheses if h.type == "bad_state_propagation"]
    assert len(bad_states) == 1
    assert "division by zero" in bad_states[0].reasoning_summary.lower()


def test_rca_cli_output(temp_project_root):
    trace_file = temp_project_root / "trace.txt"
    trace_file.write_text(
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 3, in foo\n"
        "    x = 1 / 0\n"
        "ZeroDivisionError: division by zero\n",
        encoding="utf-8"
    )
    
    # Write a .env.example to trigger env mismatch to verify CLI output
    (temp_project_root / ".env.example").write_text("PORT=8000\n", encoding="utf-8")
    
    with patch("sys.argv", ["burrow", "-r", str(temp_project_root), "analyze", str(trace_file)]):
        with patch("burrow.cli.main.console.print") as mock_console_print:
            main()
            assert mock_console_print.called
            
            # Print calls capture
            found_rca_panel = False
            for call in mock_console_print.call_args_list:
                if call[0]:
                    arg = call[0][0]
                    # Check title of Panel objects
                    if hasattr(arg, "title") and arg.title and "AI ROOT CAUSE RANKED HYPOTHESES" in str(arg.title):
                        found_rca_panel = True
                    # Fallback string checks
                    if "AI ROOT CAUSE RANKED HYPOTHESES" in str(arg):
                        found_rca_panel = True
            
            assert found_rca_panel
