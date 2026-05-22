from burrow.core.engine import BurrowEngine

PYTHON_TRACE = """
Traceback (most recent call last):
  File "app.py", line 3, in foo
    x = 1 / 0
ZeroDivisionError: division by zero
"""

def test_engine_pipeline(temp_project_root):
    # Ingest traceback, resolving against the temporary project root
    engine = BurrowEngine(project_root=temp_project_root)
    result = engine.analyze_content(PYTHON_TRACE)
    
    assert result.error.error_type == "ZeroDivisionError"
    # Local path should be resolved and mapped inside error model
    assert result.error.frames[0].file_path == (temp_project_root / "app.py").resolve().as_posix()
    assert result.error.frames[0].code_context is not None
    
    # Recommendation should exist
    assert "division or modulo operation" in result.recommendation.cause.lower()
    assert "guard condition" in result.recommendation.remediation.lower()
    
    # Graph nodes and edges should be populated
    nodes = result.graph["nodes"]
    edges = result.graph["edges"]
    
    # We expect nodes for the error, the file, and the frame
    assert any(n["type"] == "error" for n in nodes.values())
    assert any(n["type"] == "file" for n in nodes.values())
    assert any(n["type"] == "frame" for n in nodes.values())
    
    # There should be relations: file -> contains -> frame, frame -> raises -> error
    assert any(e["type"] == "contains" for e in edges)
    assert any(e["type"] == "raises" for e in edges)
