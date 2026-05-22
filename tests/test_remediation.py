import pytest
from pathlib import Path
from burrow.parser.models import NormalizedError, NormalizedFrame
from burrow.workspace.models import WorkspaceContext, WorkspaceStructure, GitContext, GitFileStatus
from burrow.symbol.models import SymbolGraphData, CodeSmell
from burrow.rca.models import RCAResult, Hypothesis
from burrow.llm.base import LLMRecommendation
from burrow.remediation.models import FixSuggestion
from burrow.remediation.engine import RemediationEngine
from burrow.core.engine import BurrowEngine

@pytest.fixture
def base_error():
    frame = NormalizedFrame(
        file_path="app.py",
        line_number=5,
        function_name="divide",
        raw_line="val = x / y",
        is_application_code=True
    )
    return NormalizedError(
        error_type="ZeroDivisionError",
        message="division by zero",
        frames=[frame],
        language="python",
        raw_input="Traceback:\n...",
        confidence_score=1.0
    )

def test_remediation_config_and_env():
    engine = RemediationEngine(project_root=Path("."))
    
    error = NormalizedError(
        error_type="ValueError",
        message="Invalid config value",
        frames=[],
        raw_input="Error"
    )
    
    rca_res = RCAResult(
        hypotheses=[
            Hypothesis(
                type="config_issue",
                root_cause="Missing configuration file .env",
                origin_file=".env",
                reasoning_summary="No .env file found.",
                safest_fix_direction="Create .env from template",
                confidence_score=0.95
            ),
            Hypothesis(
                type="env_mismatch",
                root_cause="Missing environment variable: DATABASE_URL",
                origin_file=".env",
                reasoning_summary="DATABASE_URL is not set.",
                safest_fix_direction="Add DATABASE_URL to .env",
                confidence_score=0.9
            )
        ]
    )
    
    workspace = WorkspaceContext(
        structure=WorkspaceStructure(
            entrypoints=["main.py"]
        )
    )
    
    res = engine.generate_suggestions(error, workspace_context=workspace, rca_result=rca_res)
    
    # We expect config_issue fix, env_mismatch fix, and env loading order fix
    descriptions = [s.description for s in res.suggestions]
    assert any("Create local environment configuration file" in d for d in descriptions)
    assert any("DATABASE_URL" in d for d in descriptions)
    assert any("Ensure environment variables are loaded" in d for d in descriptions)
    
    # Assert they are marked as safe
    for s in res.suggestions:
        if "Create local" in s.description or "DATABASE_URL" in s.description:
            assert s.risk_level == "safe"
            
    # Verify env loading order affected file matches the entrypoint
    order_fix = next(s for s in res.suggestions if "Ensure environment variables" in s.description)
    assert order_fix.affected_file == "main.py"

def test_remediation_zero_division(base_error):
    engine = RemediationEngine(project_root=Path("."))
    res = engine.generate_suggestions(base_error)
    
    assert len(res.suggestions) == 1
    sug = res.suggestions[0]
    assert "divisor guard" in sug.description.lower()
    assert sug.affected_file == "app.py"
    assert sug.likely_edit_region == "Line 5"
    assert sug.risk_level == "safe"
    assert "if divisor == 0:" in sug.patch_preview

def test_remediation_null_guards():
    engine = RemediationEngine(project_root=Path("."))
    error = NormalizedError(
        error_type="AttributeError",
        message="'NoneType' object has no attribute 'foo'",
        frames=[],
        raw_input="Error"
    )
    
    symbol_data = SymbolGraphData(
        smells=[
            CodeSmell(
                smell_type="null_dereference",
                message="Variable 'user' may be null before accessing property 'foo'",
                file_path="app.py",
                line_number=10,
                severity="warning"
            )
        ]
    )
    
    res = engine.generate_suggestions(error, symbol_graph_data=symbol_data)
    assert len(res.suggestions) == 1
    sug = res.suggestions[0]
    assert "null guard" in sug.description.lower()
    assert sug.affected_file == "app.py"
    assert sug.likely_edit_region == "Line 10"
    assert sug.risk_level == "safe"

def test_remediation_ranking_logic(base_error):
    engine = RemediationEngine(project_root=Path("."))
    
    # Generate some suggestions spanning safe, medium, risky
    # Let's pass an AI recommendation to trigger a medium/risky suggestion
    rec = LLMRecommendation(
        cause="Division by zero",
        remediation="Make structural change:\n```python\nx = 1\ny = 2\n```",
        confidence=0.9,
        related_files=["app.py"]
    )
    
    # We also pass a zero division error to trigger a 'safe' check
    res = engine.generate_suggestions(base_error, recommendation=rec)
    
    # Assert suggestions are sorted: safe -> medium/risky
    assert len(res.suggestions) > 1
    assert res.suggestions[0].risk_level == "safe"
    assert res.suggestions[-1].risk_level in ("medium", "risky")

def test_cli_diagnostic_remediation_output(capsys, temp_project_root):
    # Simulate a division by zero error run through the CLI
    trace = """
Traceback (most recent call last):
  File "app.py", line 3, in foo
    x = 1 / 0
ZeroDivisionError: division by zero
"""
    # Write app.py in temp root so analyzer can parse context
    (temp_project_root / "app.py").write_text("def foo():\n    x = 1 / 0\n", encoding="utf-8")
    
    engine = BurrowEngine(project_root=temp_project_root)
    result = engine.analyze_content(trace)
    
    # Verify suggestion was generated on the result
    assert result.remediation_result is not None
    assert len(result.remediation_result.suggestions) >= 1
    
    # Let's call the CLI handler directly to inspect output
    from burrow.cli.main import handle_analyze
    import argparse
    
    class MockArgs:
        input = str(temp_project_root / "trace.txt")
        project_root = temp_project_root
        llm_provider = "mock"
        format = "text"
        log_level = "info"
        
    (temp_project_root / "trace.txt").write_text(trace, encoding="utf-8")
    
    handle_analyze(MockArgs())
    captured = capsys.readouterr()
    
    # CLI printout should contain remediation section
    assert "RECOMMENDED REMEDIATION & PATCH SUGGESTIONS" in captured.out
    assert "SAFE" in captured.out
    assert "divisor guard" in captured.out
    assert "app.py" in captured.out
