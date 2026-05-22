import pytest
from burrow.parser.stacktrace import PythonStackTraceParser, JavaScriptStackTraceParser, get_parser
from burrow.parser.log import LogParser
from burrow.parser.base import ParsingError

PYTHON_TRACEBACK = """
Traceback (most recent call last):
  File "app.py", line 3, in foo
    x = 1 / 0
ZeroDivisionError: division by zero
"""

JS_TRACEBACK = """
TypeError: Cannot read properties of undefined (reading 'value')
    at test (index.js:3:25)
    at Object.<anonymous> (index.js:6:1)
"""

LOG_WITH_TRACEBACK = """
2026-05-22 10:00:00 [INFO] app: Starting execution...
2026-05-22 10:00:01 [ERROR] app: Unhandled exception occurred:
Traceback (most recent call last):
  File "app.py", line 3, in foo
    x = 1 / 0
ZeroDivisionError: division by zero
2026-05-22 10:00:02 [INFO] app: Execution finished.
"""

def test_python_parser():
    parser = PythonStackTraceParser()
    assert parser.can_parse(PYTHON_TRACEBACK) is True
    
    error = parser.parse(PYTHON_TRACEBACK)
    assert error.error_type == "ZeroDivisionError"
    assert error.message == "division by zero"
    assert error.language == "python"
    assert len(error.frames) == 1
    
    frame = error.frames[0]
    assert frame.file_path == "app.py"
    assert frame.line_number == 3
    assert frame.function_name == "foo"
    assert frame.code_context == "x = 1 / 0"


def test_javascript_parser():
    parser = JavaScriptStackTraceParser()
    assert parser.can_parse(JS_TRACEBACK) is True
    
    error = parser.parse(JS_TRACEBACK)
    assert error.error_type == "TypeError"
    assert error.message == "Cannot read properties of undefined (reading 'value')"
    assert error.language == "javascript"
    assert len(error.frames) == 2
    
    frame0 = error.frames[0]
    assert frame0.file_path == "index.js"
    assert frame0.line_number == 3
    assert frame0.function_name == "test"


def test_log_parser_extraction():
    parser = LogParser()
    assert parser.can_parse(LOG_WITH_TRACEBACK) is True
    
    error = parser.parse(LOG_WITH_TRACEBACK)
    assert error.error_type == "ZeroDivisionError"
    assert error.message == "division by zero"
    assert error.language == "python"
    assert len(error.frames) == 1


def test_invalid_parse():
    parser = PythonStackTraceParser()
    with pytest.raises(ParsingError):
        parser.parse("Just some random text without traceback structure")


def test_chained_python_exceptions():
    chained_trace = """
Traceback (most recent call last):
  File "db.py", line 5, in connect
    raise ConnectionError("Database is down")
ConnectionError: Database is down

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "app.py", line 10, in main
    connect()
  File "app.py", line 7, in connect
    raise RuntimeError("Failed to initialize app")
RuntimeError: Failed to initialize app
"""
    parser = PythonStackTraceParser()
    assert parser.can_parse(chained_trace) is True
    
    error = parser.parse(chained_trace)
    assert error.error_type == "RuntimeError"
    assert error.message == "Failed to initialize app"
    assert len(error.frames) == 2
    assert error.frames[0].file_path == "app.py"
    assert error.frames[0].line_number == 10
    assert error.frames[1].file_path == "app.py"
    assert error.frames[1].line_number == 7
    
    assert len(error.chained_errors) == 1
    chained = error.chained_errors[0]
    assert chained.error_type == "ConnectionError"
    assert chained.message == "Database is down"
    assert len(chained.frames) == 1
    assert chained.frames[0].file_path == "db.py"
    assert chained.frames[0].line_number == 5


def test_docker_log_prefix_stripping():
    docker_trace = """
web-1      | 2026-05-22T10:00:00.123456Z Traceback (most recent call last):
web-1      | 2026-05-22T10:00:00.123456Z   File "app.py", line 3, in foo
web-1      | 2026-05-22T10:00:00.123456Z     x = 1 / 0
web-1      | 2026-05-22T10:00:00.123456Z ZeroDivisionError: division by zero
"""
    parser = LogParser()
    assert parser.can_parse(docker_trace) is True
    
    error = parser.parse(docker_trace)
    assert error.error_type == "ZeroDivisionError"
    assert error.message == "division by zero"
    assert error.language == "python"
    assert error.metadata.get("docker_stripped") is True
    assert len(error.frames) == 1
    assert error.frames[0].file_path == "app.py"
    assert error.frames[0].line_number == 3


def test_pytest_log_parsing():
    pytest_trace = """
tests/test_math.py:12: in test_divide
    divide(1, 0)
tests/test_math.py:5: in divide
    return a / b
E   ZeroDivisionError: division by zero
"""
    parser = PythonStackTraceParser()
    assert parser.can_parse(pytest_trace) is True
    
    error = parser.parse(pytest_trace)
    assert error.error_type == "ZeroDivisionError"
    assert error.message == "division by zero"
    assert len(error.frames) == 2
    assert error.frames[0].file_path == "tests/test_math.py"
    assert error.frames[0].line_number == 12
    assert error.frames[1].file_path == "tests/test_math.py"
    assert error.frames[1].line_number == 5


def test_webpack_react_js_parsing():
    webpack_trace = """
TypeError: Cannot read properties of undefined (reading 'foo')
    at App (webpack:///./src/App.js:10:20)
    at renderWithHooks (webpack-internal:///./node_modules/react-dom/cjs/react-dom.development.js:14985:18)
"""
    parser = JavaScriptStackTraceParser()
    assert parser.can_parse(webpack_trace) is True
    
    error = parser.parse(webpack_trace)
    assert error.error_type == "TypeError"
    assert error.message == "Cannot read properties of undefined (reading 'foo')"
    assert len(error.frames) == 2
    
    # App code frame
    assert error.frames[0].file_path == "src/App.js"
    assert error.frames[0].line_number == 10
    assert error.frames[0].column_number == 20
    assert error.frames[0].is_application_code is True
    
    # Library code frame
    assert error.frames[1].file_path == "node_modules/react-dom/cjs/react-dom.development.js"
    assert error.frames[1].line_number == 14985
    assert error.frames[1].column_number == 18
    assert error.frames[1].is_application_code is False


def test_generic_compiler_cli_parsing():
    gcc_error = "main.c:15:3: error: expected ';' before 'return'"
    go_error = "main.go:42: undefined: fmt.Printlnn"
    ts_error = "src/index.ts(23,10): error TS2345: Argument of type 'string' is not assignable"
    
    from burrow.parser.generic import GenericCliParser
    parser = GenericCliParser()
    
    # GCC
    assert parser.can_parse(gcc_error) is True
    err_gcc = parser.parse(gcc_error)
    assert err_gcc.error_type == "error"
    assert err_gcc.message == "expected ';' before 'return'"
    assert len(err_gcc.frames) == 1
    assert err_gcc.frames[0].file_path == "main.c"
    assert err_gcc.frames[0].line_number == 15
    assert err_gcc.frames[0].column_number == 3
    
    # Go
    assert parser.can_parse(go_error) is True
    err_go = parser.parse(go_error)
    assert err_go.error_type == "CLIError"
    assert err_go.message == "undefined: fmt.Printlnn"
    assert len(err_go.frames) == 1
    assert err_go.frames[0].file_path == "main.go"
    assert err_go.frames[0].line_number == 42
    
    # TS
    assert parser.can_parse(ts_error) is True
    err_ts = parser.parse(ts_error)
    assert err_ts.error_type == "error TS2345"
    assert err_ts.message == "Argument of type 'string' is not assignable"
    assert len(err_ts.frames) == 1
    assert err_ts.frames[0].file_path == "src/index.ts"
    assert err_ts.frames[0].line_number == 23
    assert err_ts.frames[0].column_number == 10


def test_root_origin_and_surfaced_crash_point():
    # JavaScript: first frame is crash, but root origin is deepest application code frame (which is first app code frame)
    # Let's test a trace where first frame is node_modules, and second frame is src/App.js
    js_trace = """
TypeError: Cannot read properties of undefined
    at runInternal (node_modules/lib.js:5:10)
    at process (src/App.js:20:15)
    at runOuter (node_modules/outer.js:100:2)
"""
    parser = JavaScriptStackTraceParser()
    error = parser.parse(js_trace)
    
    # Surfaced crash point should be runInternal (first frame)
    assert error.surfaced_crash_point.file_path == "node_modules/lib.js"
    assert error.surfaced_crash_point.line_number == 5
    
    # Root origin should be process (deepest app frame, which is the first app frame)
    assert error.root_origin.file_path == "src/App.js"
    assert error.root_origin.line_number == 20
    
    # Python: last frame is crash, root origin is last app frame
    py_trace = """
Traceback (most recent call last):
  File "lib/wrapper.py", line 120, in start
    main()
  File "src/main.py", line 15, in main
    do_work()
  File "src/main.py", line 8, in do_work
    lib_func()
  File "lib/helper.py", line 10, in lib_func
    raise ValueError("invalid")
ValueError: invalid
"""
    py_parser = PythonStackTraceParser()
    py_error = py_parser.parse(py_trace)
    
    # Surfaced crash point should be lib/helper.py (last frame)
    assert py_error.surfaced_crash_point.file_path == "lib/helper.py"
    assert py_error.surfaced_crash_point.line_number == 10
    
    # Root origin should be src/main.py line 8 (last/deepest app frame)
    assert py_error.root_origin.file_path == "src/main.py"
    assert py_error.root_origin.line_number == 8

