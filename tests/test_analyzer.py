from burrow.parser.models import NormalizedError, NormalizedFrame
from burrow.analyzer.local_source import LocalSourceAnalyzer

def test_source_analyzer_resolves_context(temp_project_root):
    analyzer = LocalSourceAnalyzer(temp_project_root)
    
    # Construct an error with frame pointing to relative path
    error = NormalizedError(
        error_type="ZeroDivisionError",
        message="division by zero",
        language="python",
        raw_input="",
        frames=[
            NormalizedFrame(
                file_path="app.py",
                line_number=3,
                function_name="foo",
                raw_line="x = 1 / 0"
            )
        ]
    )
    
    analyzer.analyze(error)
    
    frame = error.frames[0]
    # Check that file path has been resolved to absolute path
    assert frame.file_path == (temp_project_root / "app.py").resolve().as_posix()
    assert frame.code_context is not None
    # Crashing line should be flagged with '=>' marker
    assert "=>    3 |     x = 1 / 0" in frame.code_context
    assert "   2 |     print('hello')" in frame.code_context
    assert frame.is_application_code is True
