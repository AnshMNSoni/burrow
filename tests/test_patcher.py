import pytest
import os
import json
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from burrow.remediation.patcher import Patcher
from burrow.remediation.models import FixSuggestion
from burrow.config import settings

@pytest.fixture
def temp_project():
    """Creates a temporary workspace with standard files for testing the patcher."""
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir).resolve()
        
        # Create some starting files
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "main.py").write_text("print('hello world')\nline2\nline3\n", encoding="utf-8")
        (root / "src" / "helper.py").write_text("def run():\n    pass\n", encoding="utf-8")
        (root / "config.json").write_text('{"debug": true}', encoding="utf-8")
        
        yield root

def test_trust_boundary_basic(temp_project):
    patcher = Patcher(project_root=temp_project)
    
    # 1. Path inside project root should succeed
    resolved = patcher.verify_trust_boundary("src/main.py")
    assert resolved == (temp_project / "src" / "main.py").resolve()
    
    # 2. Path traversing outside project root should raise PermissionError
    with pytest.raises(PermissionError) as exc_info:
        patcher.verify_trust_boundary("../outside.py")
    assert "lies outside project root" in str(exc_info.value)
    
    with pytest.raises(PermissionError):
        patcher.verify_trust_boundary(str(temp_project.parent / "another_folder"))

def test_trust_boundary_allowed_paths(temp_project, monkeypatch):
    patcher = Patcher(project_root=temp_project)
    
    # Restrict write paths to "src" folder only
    monkeypatch.setattr(settings, "allowed_write_paths", "src")
    
    # Path in src should be allowed
    resolved = patcher.verify_trust_boundary("src/main.py")
    assert resolved == (temp_project / "src" / "main.py").resolve()
    
    # Path in config.json is not in src, so should fail
    with pytest.raises(PermissionError) as exc_info:
        patcher.verify_trust_boundary("config.json")
    assert "not in the allowed write scope list" in str(exc_info.value)
    
    # Multiple write paths
    monkeypatch.setattr(settings, "allowed_write_paths", "src, config.json")
    assert patcher.verify_trust_boundary("config.json") == (temp_project / "config.json").resolve()
    assert patcher.verify_trust_boundary("src/helper.py") == (temp_project / "src" / "helper.py").resolve()
    
    # Subdir under allowed subdir
    (temp_project / "src" / "nested").mkdir(exist_ok=True)
    assert patcher.verify_trust_boundary("src/nested/test.py") == (temp_project / "src" / "nested" / "test.py").resolve()

def test_compute_sha256(temp_project):
    patcher = Patcher(project_root=temp_project)
    target = temp_project / "config.json"
    
    content = target.read_bytes()
    expected_hash = hashlib.sha256(content).hexdigest()
    
    assert patcher.compute_sha256(target) == expected_hash
    
    # Non-existent file
    assert patcher.compute_sha256(temp_project / "does_not_exist.txt") == ""

def test_backup_and_rollback_modification(temp_project):
    patcher = Patcher(project_root=temp_project)
    target_file = temp_project / "src" / "main.py"
    original_content = target_file.read_text(encoding="utf-8")
    
    suggestion = FixSuggestion(
        description="Fix main.py print statement",
        affected_file="src/main.py",
        risk_level="safe",
        rationale="Update test output"
    )
    
    # Backup
    meta_path = patcher.backup_file(target_file, suggestion)
    assert meta_path.exists()
    
    # Verify metadata content
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["target_file_relative"] == "src/main.py"
    assert meta["original_sha256"] == patcher.compute_sha256(target_file)
    assert meta["suggestion_description"] == suggestion.description
    
    # Modify the file
    target_file.write_text("modified content\n", encoding="utf-8")
    
    # Rollback latest
    success, msg = patcher.rollback_latest()
    assert success
    assert "Rolled back changes to src/main.py successfully" in msg
    assert target_file.read_text(encoding="utf-8") == original_content
    
    # Verify backup files were cleaned up
    assert not meta_path.exists()
    backup_file_path = temp_project / ".burrow" / "backups" / meta["backup_filename"]
    assert not backup_file_path.exists()

def test_backup_and_rollback_creation(temp_project):
    patcher = Patcher(project_root=temp_project)
    new_file = temp_project / "src" / "new_file.py"
    assert not new_file.exists()
    
    suggestion = FixSuggestion(
        description="Create a new file",
        affected_file="src/new_file.py",
        risk_level="safe",
        rationale="Add initialization code"
    )
    
    # Backup (should mark as .created backup because target file does not exist)
    meta_path = patcher.backup_file(new_file, suggestion)
    assert meta_path.exists()
    
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["target_file_relative"] == "src/new_file.py"
    assert meta["backup_filename"].endswith(".created")
    
    # Write the new file as if applied suggestion
    new_file.write_text("new content", encoding="utf-8")
    
    # Rollback latest (should delete the new file)
    success, msg = patcher.rollback_latest()
    assert success
    assert "Rolled back creation of src/new_file.py successfully" in msg
    assert not new_file.exists()
    
    # Verify backups are cleaned up
    assert not meta_path.exists()
    backup_file_path = temp_project / ".burrow" / "backups" / meta["backup_filename"]
    assert not backup_file_path.exists()

def test_rollback_multiple_backups(temp_project):
    patcher = Patcher(project_root=temp_project)
    file1 = temp_project / "src" / "main.py"
    file2 = temp_project / "src" / "helper.py"
    
    orig_content1 = file1.read_text(encoding="utf-8")
    orig_content2 = file2.read_text(encoding="utf-8")
    
    sug1 = FixSuggestion(description="Fix main", affected_file="src/main.py", rationale="R1")
    sug2 = FixSuggestion(description="Fix helper", affected_file="src/helper.py", rationale="R2")
    
    # Backup 1, then modify 1
    patcher.backup_file(file1, sug1)
    file1.write_text("new main\n", encoding="utf-8")
    
    # Backup 2, then modify 2
    patcher.backup_file(file2, sug2)
    file2.write_text("new helper\n", encoding="utf-8")
    
    # Rollback 1 (should rollback the latest backup, which is sug2 / helper)
    success, msg = patcher.rollback_latest()
    assert success
    assert "src/helper.py" in msg
    assert file2.read_text(encoding="utf-8") == orig_content2
    assert file1.read_text(encoding="utf-8") == "new main\n"
    
    # Rollback 2 (should rollback the remaining backup, which is sug1 / main)
    success, msg = patcher.rollback_latest()
    assert success
    assert "src/main.py" in msg
    assert file1.read_text(encoding="utf-8") == orig_content1
    
    # Subsequent rollbacks should fail/return false
    success, msg = patcher.rollback_latest()
    assert not success
    assert "No backups found" in msg

def test_apply_suggestion_append():
    patcher = Patcher(project_root=Path("."))
    content = "LINE1=value1\nLINE2=value2\n"
    
    # Append suggestion
    suggestion = FixSuggestion(
        description="Append custom config",
        affected_file=".env",
        likely_edit_region="append to file",
        rationale="add new variable",
        patch_preview="+NEW_VAR=hello"
    )
    
    res = patcher.apply_suggestion(content, suggestion)
    assert res == "LINE1=value1\nLINE2=value2\nNEW_VAR=hello\n"
    
    # Append without leading '+' sign (plain block)
    suggestion_plain = FixSuggestion(
        description="Append custom config",
        affected_file=".env",
        likely_edit_region="append to file",
        rationale="add new variable",
        patch_preview="```\nNEW_VAR=hello\n```"
    )
    res_plain = patcher.apply_suggestion(content, suggestion_plain)
    assert res_plain == "LINE1=value1\nLINE2=value2\nNEW_VAR=hello\n"

def test_apply_suggestion_prepend():
    patcher = Patcher(project_root=Path("."))
    content = "def test():\n    pass\n"
    
    # Prepend suggestion
    suggestion = FixSuggestion(
        description="Prepend import statement",
        affected_file="app.py",
        likely_edit_region="first lines",
        rationale="add import",
        patch_preview="+ import os\nimport sys"
    )
    
    res = patcher.apply_suggestion(content, suggestion)
    assert res == "import os\nimport sys\ndef test():\n    pass\n"

def test_apply_suggestion_replace_line():
    patcher = Patcher(project_root=Path("."))
    content = "line1\nline2\nline3\n"
    
    suggestion = FixSuggestion(
        description="Replace second line",
        affected_file="app.py",
        likely_edit_region="line 2",
        rationale="correct bug",
        patch_preview="-line2\n+line2_updated"
    )
    
    res = patcher.apply_suggestion(content, suggestion)
    assert res == "line1\nline2_updated\nline3\n"

def test_apply_suggestion_unified_diff_fallback():
    patcher = Patcher(project_root=Path("."))
    content = "old content\n"
    
    suggestion = FixSuggestion(
        description="Replace using unified diff style fallback",
        affected_file="app.py",
        likely_edit_region="middle of file",
        rationale="fallback check",
        patch_preview="-old content\n+new content"
    )
    
    res = patcher.apply_suggestion(content, suggestion)
    assert res == "new content\n"

def test_apply_suggestion_generic_fallback():
    patcher = Patcher(project_root=Path("."))
    content = "existing code\n"
    
    suggestion = FixSuggestion(
        description="Apply suggestions using normal block fallback",
        affected_file="app.py",
        likely_edit_region="random lines",
        rationale="fallback check",
        patch_preview="# some comments\nx = 1"
    )
    
    res = patcher.apply_suggestion(content, suggestion)
    assert "Burrow Suggested Remedy" in res
    assert "x = 1" in res
