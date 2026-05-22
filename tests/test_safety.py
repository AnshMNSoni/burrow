import pytest
import os
import json
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from burrow.remediation.patcher import Patcher
from burrow.remediation.models import FixSuggestion
from burrow.config import settings
from burrow.cli.main import display_analysis_result, handle_patch

@pytest.fixture
def temp_project():
    """Creates a temporary workspace with standard files for testing."""
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir).resolve()
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
        yield root

def test_verify_patch_safety_mismatch(temp_project):
    patcher = Patcher(project_root=temp_project)
    target_file = temp_project / "src" / "main.py"
    
    # 1. Matches expected hash
    original_sha = patcher.compute_sha256(target_file)
    suggestion = FixSuggestion(
        description="Fix print statement",
        affected_file="src/main.py",
        rationale="Update text",
        original_sha256=original_sha
    )
    # Should not raise any error
    patcher.verify_patch_safety(suggestion)

    # 2. Hashes mismatch (file was modified)
    suggestion_mismatch = FixSuggestion(
        description="Fix print statement",
        affected_file="src/main.py",
        rationale="Update text",
        original_sha256="different_sha256_hash_value"
    )
    with pytest.raises(ValueError) as exc:
        patcher.verify_patch_safety(suggestion_mismatch)
    assert "Integrity Check Failed" in str(exc.value)
    assert "has been modified" in str(exc.value)

def test_verify_patch_safety_existence_mismatch(temp_project):
    patcher = Patcher(project_root=temp_project)
    
    # 1. File expected to exist, but does not
    suggestion = FixSuggestion(
        description="Fix missing file",
        affected_file="src/non_existent.py",
        rationale="Update missing",
        original_sha256="some_hash_value"
    )
    with pytest.raises(ValueError) as exc:
        patcher.verify_patch_safety(suggestion)
    assert "was expected to exist, but it does not exist" in str(exc.value)

    # 2. File expected to NOT exist, but it now does
    suggestion_not_exist = FixSuggestion(
        description="Create new file",
        affected_file="src/main.py",
        rationale="Overwrites existing file unexpectedly",
        original_sha256=None
    )
    with pytest.raises(ValueError) as exc:
        patcher.verify_patch_safety(suggestion_not_exist)
    assert "was expected to NOT exist, but it now exists" in str(exc.value)

def test_rollback_atomic_failures(temp_project):
    patcher = Patcher(project_root=temp_project)
    target_file = temp_project / "src" / "main.py"
    original_sha = patcher.compute_sha256(target_file)
    
    suggestion = FixSuggestion(
        description="Fix main",
        affected_file="src/main.py",
        rationale="Update",
        original_sha256=original_sha
    )
    
    # Create backup
    meta_path = patcher.backup_file(target_file, suggestion)
    assert meta_path.exists()
    
    # Now simulate write failure during restoration (e.g. by making target file's parent dir write-protected)
    # We can mock os.replace to raise OSError
    with patch("os.replace", side_effect=OSError("Disk Full or Lock error")):
        success, msg = patcher.rollback_latest()
        assert not success
        assert "Failed to perform rollback" in msg
        
        # Verify backup files were NOT deleted (transaction preserved on failure)
        assert meta_path.exists()
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        backup_file_path = temp_project / ".burrow" / "backups" / meta["backup_filename"]
        assert backup_file_path.exists()

def test_display_analysis_result_mock_safety():
    # Verify that passing Mock objects doesn't raise exception
    mock_result = Mock()
    display_analysis_result(mock_result)

def test_handle_patch_low_confidence_blocked(temp_project, monkeypatch):
    monkeypatch.setattr(settings, "patch_min_confidence", 0.30)
    monkeypatch.setattr(settings, "weak_confidence_threshold", 0.60)
    monkeypatch.setattr(settings, "enable_auto_patch", True)

    suggestion = FixSuggestion(
        description="Risky fix",
        affected_file="src/main.py",
        rationale="Updates core",
        risk_level="safe",
        confidence_score=0.45,  # Below weak confidence threshold of 0.60
        original_sha256="",
        patch_preview="+print('new_patch')"
    )
    
    # Mock engine and suggestion results
    mock_engine = Mock()
    mock_result = Mock()
    mock_result.remediation_result.suggestions = [suggestion]
    mock_engine.analyze_content.return_value = mock_result
    
    args = Mock()
    args.project_root = str(temp_project)
    args.llm_provider = "mock"
    args.log_level = "info"
    args.input = "-"
    args.suggestion_index = 0
    args.yes = True  # Non-interactive mode
    args.rollback = False
    
    # Setup test file original content matching suggestion expectation (non-existent or empty string)
    (temp_project / "src" / "main.py").unlink()
    
    with patch("burrow.cli.main.BurrowEngine", return_value=mock_engine), \
         patch("burrow.cli.main.read_input", return_value="some log trace"), \
         pytest.raises(SystemExit) as exc_info:
        handle_patch(args)
        
    assert exc_info.value.code == 1

def test_handle_patch_risky_blocked(temp_project, monkeypatch):
    monkeypatch.setattr(settings, "patch_min_confidence", 0.30)
    monkeypatch.setattr(settings, "weak_confidence_threshold", 0.60)
    monkeypatch.setattr(settings, "enable_auto_patch", True)

    # Suggestion is risky, even if confidence is high (0.80)
    suggestion = FixSuggestion(
        description="Risky fix",
        affected_file="src/main.py",
        rationale="Updates core",
        risk_level="risky",
        confidence_score=0.80,
        original_sha256="",
        patch_preview="+print('new_patch')"
    )
    
    mock_engine = Mock()
    mock_result = Mock()
    mock_result.remediation_result.suggestions = [suggestion]
    mock_engine.analyze_content.return_value = mock_result
    
    args = Mock()
    args.project_root = str(temp_project)
    args.llm_provider = "mock"
    args.log_level = "info"
    args.input = "-"
    args.suggestion_index = 0
    args.yes = True  # Non-interactive mode
    args.rollback = False
    
    (temp_project / "src" / "main.py").unlink()
    
    with patch("burrow.cli.main.BurrowEngine", return_value=mock_engine), \
         patch("burrow.cli.main.read_input", return_value="some log trace"), \
         pytest.raises(SystemExit) as exc_info:
        handle_patch(args)
        
    assert exc_info.value.code == 1
