import os
import re
import json
import hashlib
import difflib
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime

from burrow.remediation.models import FixSuggestion
from burrow.config import settings

class Patcher:
    """Utility class to manage path security, file integrity validation, diff generation, file backup, and rollback operations."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()

    def verify_trust_boundary(self, file_path: str) -> Path:
        """Resolves absolute path and verifies it doesn't violate boundaries or escape project root."""
        # 1. Path traversal escape check
        resolved = Path(self.project_root / file_path).resolve()
        if not resolved.is_relative_to(self.project_root):
            raise PermissionError(f"Security Violation: Target path '{file_path}' lies outside project root '{self.project_root}'")
        
        # 2. Allowed write paths check
        allowed = settings.allowed_write_paths
        if allowed:
            allowed_list = [p.strip() for p in allowed.split(",") if p.strip()]
            if allowed_list:
                in_allowed = False
                for allowed_rel in allowed_list:
                    allowed_abs = Path(self.project_root / allowed_rel).resolve()
                    if resolved == allowed_abs or resolved.is_relative_to(allowed_abs):
                        in_allowed = True
                        break
                if not in_allowed:
                    raise PermissionError(f"Security Violation: Path '{file_path}' is not in the allowed write scope list: {allowed}")
                    
        return resolved

    def compute_sha256(self, file_path: Path) -> str:
        """Computes SHA-256 checksum of file."""
        if not file_path.exists():
            return ""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def backup_file(self, target_file: Path, suggestion: FixSuggestion) -> Path:
        """Creates a timestamped backup copy under .burrow/backups/ and metadata log."""
        backup_dir = self.project_root / ".burrow" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        rel_path = target_file.relative_to(self.project_root).as_posix()
        safe_name = rel_path.replace("/", "_").replace("\\", "_")
        
        backup_file_path = backup_dir / f"{timestamp}_{safe_name}.bak"
        
        # Create metadata info
        original_sha = self.compute_sha256(target_file)
        
        if target_file.exists():
            with open(target_file, "r", encoding="utf-8", errors="ignore") as f_in:
                content = f_in.read()
            with open(backup_file_path, "w", encoding="utf-8") as f_out:
                f_out.write(content)
        else:
            # Mark file was non-existent
            backup_file_path = backup_dir / f"{timestamp}_{safe_name}.created"
            backup_file_path.touch()
            
        metadata = {
            "timestamp": timestamp,
            "target_file_relative": rel_path,
            "backup_filename": backup_file_path.name,
            "original_sha256": original_sha,
            "suggestion_description": suggestion.description
        }
        
        # Write metadata JSON file next to the backup file
        meta_path = backup_dir / f"{timestamp}_{safe_name}.json"
        with open(meta_path, "w", encoding="utf-8") as f_meta:
            json.dump(metadata, f_meta, indent=2)
            
        return meta_path

    def rollback_latest(self) -> Tuple[bool, str]:
        """Rolls back the latest patch applied, restoring the target file content."""
        backup_dir = self.project_root / ".burrow" / "backups"
        if not backup_dir.exists():
            return False, "No backups found."
            
        meta_files = list(backup_dir.glob("*.json"))
        if not meta_files:
            return False, "No backups found."
            
        # Sort metadata files by name/timestamp (descending)
        meta_files.sort(key=lambda p: p.name, reverse=True)
        latest_meta_path = meta_files[0]
        
        try:
            with open(latest_meta_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
                
            rel_path = metadata["target_file_relative"]
            target_path = self.project_root / rel_path
            backup_filename = metadata["backup_filename"]
            backup_path = backup_dir / backup_filename
            
            # Perform restoration
            if backup_path.name.endswith(".created"):
                # File was created, so rollback should delete it
                if target_path.exists():
                    target_path.unlink()
                msg = f"Rolled back creation of {rel_path} successfully."
            else:
                if not backup_path.exists():
                    return False, f"Backup file {backup_path.name} not found."
                with open(backup_path, "r", encoding="utf-8") as f_bak:
                    restored_content = f_bak.read()
                # Create parent dirs if they got deleted
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with open(target_path, "w", encoding="utf-8") as f_targ:
                    f_targ.write(restored_content)
                msg = f"Rolled back changes to {rel_path} successfully."
                
            # Remove backup artifacts
            latest_meta_path.unlink()
            backup_path.unlink()
            
            return True, msg
        except Exception as e:
            return False, f"Failed to perform rollback: {str(e)}"

    def apply_suggestion(self, original_content: str, suggestion: FixSuggestion) -> str:
        """Applies suggestion patch to the original content string (Python port of TypeScript PatchProvider)."""
        preview = suggestion.patch_preview or ""
        if not preview:
            return original_content
            
        lines = original_content.splitlines(keepends=True)
        preview_lines = preview.splitlines()
        edit_region = (suggestion.likely_edit_region or "").lower()
        
        # 1. DOTENV CONFIG APPEND
        if "append" in edit_region or "append to file" in edit_region:
            clean_additions = [line[1:].strip() for line in preview_lines if line.startswith("+")]
            if clean_additions:
                suffix = "\n".join(clean_additions) + "\n"
            else:
                # If plain block, append as is
                clean_preview = re.sub(r"^```(?:\w+)?\n", "", preview)
                clean_preview = re.sub(r"\n```$", "", clean_preview)
                suffix = clean_preview + "\n"
            
            sep = "" if original_content.endswith("\n") or not original_content else "\n"
            return original_content + sep + suffix
            
        # 2. PREPEND (First lines of file)
        if "first lines" in edit_region or "beginning" in edit_region:
            clean_preview = re.sub(r"^```(?:\w+)?\n", "", preview)
            clean_preview = re.sub(r"\n```$", "", clean_preview)
            clean_preview = re.sub(r"^\+ ", "", clean_preview) # strip leading plus
            
            sep = "" if clean_preview.endswith("\n") else "\n"
            return clean_preview + sep + original_content
            
        # 3. REPLACE A SPECIFIC LINE
        line_match = re.search(r"line\s+(\d+)", edit_region)
        if line_match:
            line_num = int(line_match.group(1))
            line_idx = line_num - 1
            if 0 <= line_idx < len(lines):
                clean_lines = []
                for line in preview_lines:
                    if line.startswith("-"):
                        continue
                    elif line.startswith("+"):
                        clean_lines.append(line[1:])
                    else:
                        clean_lines.append(line)
                        
                replacement = "\n".join(clean_lines)
                replacement = re.sub(r"^```(?:\w+)?\n", "", replacement)
                replacement = re.sub(r"\n```$", "", replacement)
                
                # Maintain original line ending if possible
                if not replacement.endswith("\n") and lines[line_idx].endswith("\n"):
                    replacement += "\n"
                elif replacement.endswith("\n") and not lines[line_idx].endswith("\n"):
                    replacement = replacement.rstrip("\r\n")
                    
                lines[line_idx] = replacement
                return "".join(lines)
                
        # 4. Fallback replacement or unified diff parsing
        cleaned_block = re.sub(r"^```(?:\w+)?\n", "", preview)
        cleaned_block = re.sub(r"\n```$", "", cleaned_block)
        
        if not original_content.strip():
            return cleaned_block
            
        # Fallback if unified diff formatting is supplied
        if "\n+" in preview or "\n-" in preview or preview.startswith("+ ") or preview.startswith("- "):
            clean_lines = []
            for line in preview_lines:
                if line.startswith("-"):
                    continue
                elif line.startswith("+"):
                    clean_lines.append(line[1:])
                else:
                    clean_lines.append(line)
            return "\n".join(clean_lines) + "\n"
            
        # Fallback comments
        return original_content + ("\n" if original_content.endswith("\n") else "\n\n") + "# Burrow Suggested Remedy:\n" + cleaned_block + "\n"

    def get_diff(self, original_content: str, patched_content: str, filename: str) -> str:
        """Generates standard unified diff output."""
        orig_lines = original_content.splitlines(keepends=True)
        patched_lines = patched_content.splitlines(keepends=True)
        
        diff = difflib.unified_diff(
            orig_lines,
            patched_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}"
        )
        return "".join(diff)
