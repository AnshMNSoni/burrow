import json
import urllib.request
import urllib.error
from unittest.mock import patch, MagicMock
import pytest
from burrow.parser.models import NormalizedError, NormalizedFrame
from burrow.llm.client import LocalOllamaClient, MockLLMClient, get_llm_client
from burrow.workspace.models import WorkspaceContext, WorkspaceStructure, GitContext, GitFileStatus, DependencyInfo
from burrow.symbol.models import SymbolGraphData, CodeSmell
from burrow.rca.models import RCAResult, Hypothesis

@pytest.fixture
def sample_error():
    frame = NormalizedFrame(
        file_path="app.py",
        line_number=3,
        function_name="foo",
        code_context="=>    3 |     x = 1 / 0",
        raw_line="x = 1 / 0",
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

@pytest.fixture
def sample_workspace():
    return WorkspaceContext(
        structure=WorkspaceStructure(
            detected_frameworks=["FastAPI"],
            package_managers=["pip"]
        ),
        dependencies={
            "pip": [DependencyInfo(name="fastapi", version="0.100.0", scope="production")]
        },
        git=GitContext(
            active_branch="main",
            recent_changes=[GitFileStatus(file_path="app.py", status="modified")]
        )
    )

@pytest.fixture
def sample_symbol():
    return SymbolGraphData(
        smells=[
            CodeSmell(
                smell_type="null_dereference",
                message="Potential null reference access",
                file_path="app.py",
                line_number=3,
                severity="error"
            )
        ]
    )

@pytest.fixture
def sample_rca():
    return RCAResult(
        hypotheses=[
            Hypothesis(
                type="recent_change",
                root_cause="Recent git modification in app.py",
                origin_file="app.py",
                line_number=3,
                reasoning_summary="Modified in git recently.",
                safest_fix_direction="Check division logic in app.py line 3.",
                confidence_score=0.9
            )
        ],
        propagation_chain=["app.py:foo"]
    )

def test_prompt_construction(sample_error, sample_workspace, sample_symbol, sample_rca):
    client = LocalOllamaClient()
    prompt = client.build_prompt(
        sample_error,
        workspace_context=sample_workspace,
        symbol_graph_data=sample_symbol,
        rca_result=sample_rca
    )
    
    # Assert key elements are in the prompt
    assert "ZeroDivisionError" in prompt
    assert "division by zero" in prompt
    assert "app.py:3" in prompt
    assert "x = 1 / 0" in prompt
    assert "FastAPI" in prompt
    assert "fastapi (0.100.0)" in prompt
    assert "NULL_DEREFERENCE" in prompt
    assert "recent_change" in prompt
    assert "app.py:foo" in prompt
    assert "Modified/Untracked Git Files: app.py (modified)" in prompt

@patch("urllib.request.urlopen")
def test_ollama_success_path(mock_urlopen, sample_error):
    # Mock Ollama API response
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "response": json.dumps({
            "cause": "Division by zero on line 3.",
            "remediation": "Ensure the denominator is not zero.",
            "confidence": 0.95,
            "related_files": ["app.py"]
        })
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response
    
    client = LocalOllamaClient()
    recommendation = client.analyze_error(sample_error)
    
    assert recommendation.cause == "Division by zero on line 3."
    assert recommendation.remediation == "Ensure the denominator is not zero."
    assert recommendation.confidence == 0.95
    assert recommendation.related_files == ["app.py"]

@patch("urllib.request.urlopen")
def test_ollama_http_failure_fallback(mock_urlopen, sample_error):
    # Mock HTTP failure (e.g. timeout or URLError)
    mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
    
    client = LocalOllamaClient()
    recommendation = client.analyze_error(sample_error)
    
    # Should fall back to MockLLMClient which outputs standard ZeroDivisionError recommendations
    assert "division or modulo operation" in recommendation.cause.lower()
    assert "guard condition" in recommendation.remediation.lower()
    assert recommendation.confidence == 0.95

@patch("urllib.request.urlopen")
def test_ollama_invalid_json_fallback(mock_urlopen, sample_error):
    # Mock Ollama returning invalid JSON
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "response": "this is not JSON"
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response
    
    client = LocalOllamaClient()
    recommendation = client.analyze_error(sample_error)
    
    # Should fall back to MockLLMClient
    assert "division or modulo operation" in recommendation.cause.lower()
    assert recommendation.confidence == 0.95

@patch("urllib.request.urlopen")
def test_ollama_missing_fields_fallback(mock_urlopen, sample_error):
    # Mock Ollama returning JSON with missing required fields
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "response": json.dumps({
            "cause": "some cause"
            # remediation is missing
        })
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response
    
    client = LocalOllamaClient()
    recommendation = client.analyze_error(sample_error)
    
    # Should fall back to MockLLMClient
    assert "division or modulo operation" in recommendation.cause.lower()
    assert recommendation.confidence == 0.95

def test_get_llm_client():
    client_mock = get_llm_client("mock")
    assert isinstance(client_mock, MockLLMClient)
    
    client_ollama = get_llm_client("ollama")
    assert isinstance(client_ollama, LocalOllamaClient)
