import sys
import pytest
from unittest.mock import patch, MagicMock
from burrow.cli.main import main

def test_cli_help():
    with patch("sys.argv", ["burrow", "--help"]):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0


def test_cli_parse_command(temp_project_root):
    # Create a dummy trace file
    trace_file = temp_project_root / "trace.txt"
    trace_file.write_text(
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 3, in foo\n"
        "    x = 1 / 0\n"
        "ZeroDivisionError: division by zero\n"
    )
    
    with patch("sys.argv", ["burrow", "-r", str(temp_project_root), "parse", str(trace_file)]):
        with patch("builtins.print") as mock_print:
            main()
            assert mock_print.called
            # The stdout print should contain JSON data
            printed_data = mock_print.call_args[0][0]
            assert "ZeroDivisionError" in printed_data


def test_cli_analyze_command(temp_project_root):
    trace_file = temp_project_root / "trace.txt"
    trace_file.write_text(
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 3, in foo\n"
        "    x = 1 / 0\n"
        "ZeroDivisionError: division by zero\n"
    )
    
    with patch("sys.argv", ["burrow", "-r", str(temp_project_root), "analyze", str(trace_file)]):
        with patch("burrow.cli.main.console.print") as mock_console_print:
            main()
            assert mock_console_print.called
            # Verify we render panels or texts
            calls = [call[0][0] for call in mock_console_print.call_args_list if call[0]]
            # Some printed item should be a Panel or mention call stack
            assert any("CALL STACK" in str(c) for c in calls)
