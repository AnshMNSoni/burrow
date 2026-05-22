import pytest
import string
import random
from burrow.parser import LogParser, PythonStackTraceParser, JavaScriptStackTraceParser
from burrow.parser.base import ParsingError
from burrow.config import settings

def test_parser_empty_and_whitespace():
    parser = LogParser()
    assert parser.can_parse("") is False
    assert parser.can_parse("   \n   \t  ") is False
    with pytest.raises(ParsingError):
        parser.parse("")

def test_parser_severe_malformation():
    parser = LogParser()
    
    # 1. Header only
    trace = "Traceback (most recent call last):\n"
    assert parser.can_parse(trace) is True
    with pytest.raises(ParsingError):
        parser.parse(trace)
        
    # 2. Incomplete Python frame
    trace_half_frame = (
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 10"
    )
    with pytest.raises(ParsingError):
        parser.parse(trace_half_frame)

    # 3. Truncated exception message
    trace_no_message = (
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 10, in main\n"
        "    foo()"
    )
    # This actually might parse the frame but set default exception type
    err = parser.parse(trace_no_message)
    assert len(err.frames) == 1
    assert err.error_type == "PythonError"  # fallback

def test_parser_massive_input():
    parser = LogParser()
    
    # Generate a massive trace with 1000 lines
    massive_lines = ["Traceback (most recent call last):"]
    for i in range(1000):
        massive_lines.append(f'  File "file_{i}.py", line {i}, in func_{i}')
        massive_lines.append(f'    x = {i}')
    massive_lines.append("ValueError: too large")
    trace = "\n".join(massive_lines)
    
    # Ensure can_parse runs fast and doesn't hang
    assert parser.can_parse(trace) is True
    err = parser.parse(trace)
    assert len(err.frames) == 1000
    assert err.error_type == "ValueError"
    assert err.message == "too large"

def test_parser_binary_and_control_chars():
    parser = LogParser()
    # Log with null bytes, bell rings, and arbitrary escape codes
    raw_log = (
        "2026-05-22T10:00:00Z \x00\x07\x1b[31mTraceback (most recent call last):\n"
        "\x00  File \"app.py\", line 3, in foo\n"
        "    x = 1 / 0\n"
        "\x07ZeroDivisionError: division by zero\n"
    )
    assert parser.can_parse(raw_log) is True
    err = parser.parse(raw_log)
    assert err.error_type == "ZeroDivisionError"
    assert err.frames[0].file_path == "app.py"

def test_parser_interleaved_exceptions():
    parser = LogParser()
    
    # A log containing both Python and JS stack traces
    interleaved = (
        "Some node warning:\n"
        "TypeError: Cannot read properties of undefined\n"
        "    at run (index.js:5:10)\n"
        "And then some Python crash:\n"
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 5, in main\n"
        "    run_js()\n"
        "RuntimeError: node process exited\n"
    )
    
    # Python takes precedence because of "Traceback"
    assert parser.can_parse(interleaved) is True
    err = parser.parse(interleaved)
    assert err.language == "python"
    assert err.error_type == "RuntimeError"
    assert err.frames[0].file_path == "app.py"

def test_parser_cyclical_and_nested_chaining():
    # A chained traceback with 5 levels of exceptions
    chained = (
        "Traceback (most recent call last):\n"
        "  File \"db.py\", line 10, in connect\n"
        "    raise OSError(\"Timeout\")\n"
        "OSError: Timeout\n"
        "\nDuring handling of the above exception, another exception occurred:\n"
        "\nTraceback (most recent call last):\n"
        "  File \"service.py\", line 20, in run\n"
        "    connect_to_db()\n"
        "  File \"service.py\", line 15, in connect_to_db\n"
        "    raise RuntimeError(\"DB connection failed\")\n"
        "RuntimeError: DB connection failed\n"
        "\nDuring handling of the above exception, another exception occurred:\n"
        "\nTraceback (most recent call last):\n"
        "  File \"app.py\", line 5, in main\n"
        "    run_service()\n"
        "AttributeError: 'NoneType' object has no attribute 'run'\n"
    )
    parser = PythonStackTraceParser()
    err = parser.parse(chained)
    
    # Final error should be AttributeError
    assert err.error_type == "AttributeError"
    assert len(err.frames) == 1
    assert err.frames[0].file_path == "app.py"
    
    # Chained errors should capture the preceding errors
    assert len(err.chained_errors) == 2
    assert err.chained_errors[0].error_type == "OSError"
    assert err.chained_errors[1].error_type == "RuntimeError"
