from pathlib import Path
from typing import Optional
from burrow.analyzer.base import BaseAnalyzer
from burrow.parser.models import NormalizedError
from burrow.utils.logging import logger

class LocalSourceAnalyzer(BaseAnalyzer):
    """Enriches NormalizedError frames with local source code file contents and context."""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()

    def resolve_path(self, file_path_str: str) -> Optional[Path]:
        """Resolves a raw file path from a stack trace to an actual local file path."""
        # Try as absolute path first
        path = Path(file_path_str)
        if path.is_absolute() and path.exists():
            return path.resolve()
            
        # Try relative to the project root
        resolved = (self.project_root / path).resolve()
        if resolved.exists():
            return resolved

        # Fallback: search recursively for files with the same filename (helps with docker/remote paths)
        filename = path.name
        if filename:
            try:
                # Limit rglob search to avoid slow operations in huge projects
                for matched_path in self.project_root.rglob(filename):
                    # Exclude typical virtualenv and build directories
                    path_parts = matched_path.parts
                    if not any(part in path_parts for part in (".git", "venv", ".venv", "node_modules", "dist", "build")):
                        return matched_path.resolve()
            except Exception as e:
                logger.debug(f"Failed to scan directory for filename {filename}: {e}")

        return None

    def get_code_context(self, path: Path, line_number: int, context_lines: int = 5) -> Optional[str]:
        """Extracts context lines around the target line number (1-indexed)."""
        if not path.exists() or not path.is_file():
            return None
            
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except Exception as e:
            logger.warning(f"Failed to read file {path}: {e}")
            return None

        # Convert to 0-indexed line number
        target_idx = line_number - 1
        if target_idx < 0 or target_idx >= len(lines):
            return None

        start = max(0, target_idx - context_lines)
        end = min(len(lines), target_idx + context_lines + 1)

        context_lines_list = []
        for idx in range(start, end):
            line_content = lines[idx].rstrip("\n")
            prefix = "=> " if idx == target_idx else "   "
            context_lines_list.append(f"{prefix}{idx + 1:4d} | {line_content}")

        return "\n".join(context_lines_list)

    def analyze(self, error: NormalizedError) -> None:
        """Finds source file paths and populates NormalizedFrame.code_context recursively."""
        for frame in error.frames:
            if not frame.line_number:
                continue
                
            resolved_path = self.resolve_path(frame.file_path)
            if resolved_path:
                # Update to local absolute path representation
                frame.file_path = str(resolved_path.as_posix())
                
                context = self.get_code_context(resolved_path, frame.line_number)
                if context:
                    frame.code_context = context
                
                # Check if it resides inside project root and not in virtual environments
                try:
                    is_sub = resolved_path.relative_to(self.project_root)
                    in_venv = any(part in resolved_path.parts for part in (".venv", "venv", "site-packages"))
                    frame.is_application_code = not in_venv
                except ValueError:
                    frame.is_application_code = False

        # Recursively resolve chained errors
        for chained in error.chained_errors:
            self.analyze(chained)

