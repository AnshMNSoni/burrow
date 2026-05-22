from pathlib import Path
from typing import List, Optional, Set, Dict, Any
import re
import os

from burrow.parser.models import NormalizedError, NormalizedFrame
from burrow.workspace.models import WorkspaceContext
from burrow.symbol.models import SymbolGraphData, CodeSmell
from burrow.rca.models import Hypothesis, RCAResult
from burrow.utils.logging import logger

class RootCauseAnalyzer:
    """Statically analyzes stack traces and project context to infer root cause hypotheses."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()

    def _make_relative(self, file_path: Optional[str]) -> str:
        if not file_path:
            return ""
        try:
            resolved = Path(file_path).resolve()
            # In Windows, paths must match drive letter to compute relative path.
            # Using is_relative_to is safe if we resolve both.
            if resolved.is_relative_to(self.project_root):
                return resolved.relative_to(self.project_root).as_posix()
        except Exception:
            pass
        return file_path

    def _parse_env_keys(self, path: Path) -> Set[str]:
        keys = set()
        if not path.exists():
            return keys
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" in line:
                    parts = line.split("=", 1)
                    key = parts[0].strip()
                    # Strip any trailing space or trailing quotes
                    key = key.split()[0] if key.split() else key
                    keys.add(key)
        except Exception as e:
            logger.error(f"Failed parsing env file {path}: {e}")
        return keys

    def _extract_env_keys_from_traceback(self, error: NormalizedError) -> List[Dict[str, Any]]:
        refs = []
        env_regex = re.compile(
            r'os\.environ\.get\(\s*["\'](\w+)["\']|'
            r'os\.environ\[\s*["\'](\w+)["\']\]|'
            r'os\.getenv\(\s*["\'](\w+)["\']|'
            r'process\.env\.(\w+)|'
            r'process\.env\[\s*["\'](\w+)["\']\]'
        )

        def check_frame(frame):
            if not frame.code_context and not frame.raw_line:
                return
            context = frame.code_context or frame.raw_line
            matches = env_regex.findall(context)
            for m in matches:
                key = next((g for g in m if g), None)
                if key:
                    refs.append({
                        "key": key,
                        "frame": frame,
                        "file": frame.file_path,
                        "line": frame.line_number
                    })

        def check_error(err):
            for frame in err.frames:
                check_frame(frame)
            for chained in err.chained_errors:
                check_error(chained)

        check_error(error)
        return refs

    def analyze(
        self,
        error: NormalizedError,
        workspace_context: Optional[WorkspaceContext] = None,
        symbol_graph_data: Optional[SymbolGraphData] = None
    ) -> RCAResult:
        logger.info("Starting Root Cause Analysis static heuristics run.")
        hypotheses: List[Hypothesis] = []

        # 1. Parse env / config
        dot_env_path = self.project_root / ".env"
        dot_env_example_path = self.project_root / ".env.example"

        env_keys = self._parse_env_keys(dot_env_path)
        env_example_keys = self._parse_env_keys(dot_env_example_path)

        has_dot_env = dot_env_path.exists()
        missing_example_keys = env_example_keys - env_keys if has_dot_env else env_example_keys

        # Inspect traceback frames for env variable reads
        env_keys_referenced = self._extract_env_keys_from_traceback(error)

        # Heuristic 1a: Missing .env file
        if not has_dot_env:
            impacted = list({ref["file"] for ref in env_keys_referenced if ref["file"]})
            hypotheses.append(Hypothesis(
                type="config_issue",
                root_cause="The .env configuration file is missing from the project root.",
                origin_file=".env",
                line_number=None,
                probable_impacted_modules=impacted,
                reasoning_summary="An environment configuration file (.env) was not found, but env-dependency patterns or .env.example were found in the project workspace.",
                safest_fix_direction="Create a '.env' file in the project root and populate it with required configuration variables.",
                confidence_score=0.95 if (env_example_keys or env_keys_referenced) else 0.50
            ))

        # Heuristic 1b: Missing keys referenced in the traceback
        for ref in env_keys_referenced:
            key = ref["key"]
            if key not in env_keys:
                conf = 0.80 if not has_dot_env else 0.90
                hypotheses.append(Hypothesis(
                    type="env_mismatch",
                    root_cause=f"Environment variable '{key}' is read in code but missing from the active .env configuration.",
                    origin_file=self._make_relative(ref["file"]),
                    line_number=ref["line"],
                    probable_impacted_modules=[ref["frame"].module_name] if ref["frame"].module_name else [],
                    reasoning_summary=f"The application tried to read the environment variable '{key}' at {self._make_relative(ref['file'])}:{ref['line']}, but it is not set in the active .env file.",
                    safest_fix_direction=f"Add '{key}=your_value_here' to the '.env' file in the project root.",
                    confidence_score=conf
                ))

        # Heuristic 1c: Missing .env.example keys in .env
        if has_dot_env and missing_example_keys:
            hypotheses.append(Hypothesis(
                type="env_mismatch",
                root_cause=f"Environment keys defined in .env.example are missing in .env: {', '.join(sorted(missing_example_keys))}",
                origin_file=".env",
                line_number=None,
                probable_impacted_modules=[],
                reasoning_summary="Some keys present in '.env.example' are not defined in the local '.env' file, which might cause configuration errors at runtime.",
                safest_fix_direction="Ensure all keys from '.env.example' are copied and configured in your local '.env' file.",
                confidence_score=0.75
            ))

        # 2. Git recent changes
        def get_all_frames(err):
            frames = list(err.frames)
            for chained in err.chained_errors:
                frames.extend(get_all_frames(chained))
            return frames

        all_frames = get_all_frames(error)
        surfaced_crash_frame = error.surfaced_crash_point or (error.frames[-1] if error.frames else None)

        for frame in all_frames:
            git_status = frame.metadata.get("git_status")
            if git_status:
                is_crash_frame = (surfaced_crash_frame and frame.file_path == surfaced_crash_frame.file_path and frame.line_number == surfaced_crash_frame.line_number)
                confidence = 0.95 if is_crash_frame else 0.80

                hypotheses.append(Hypothesis(
                    type="recent_change",
                    root_cause=f"Recent modification in file '{self._make_relative(frame.file_path)}' (Git status: {git_status}).",
                    origin_file=self._make_relative(frame.file_path),
                    line_number=frame.line_number,
                    probable_impacted_modules=[frame.module_name] if frame.module_name else [],
                    reasoning_summary=f"The file '{self._make_relative(frame.file_path)}' was recently {git_status} in git and is executing when the error occurred. Changes to this file may have introduced the regression.",
                    safest_fix_direction="Review the recent git changes or diff for this file around the exception call path.",
                    confidence_score=confidence
                ))

        # 3. Code Smell Correlation (AST Smells)
        if symbol_graph_data and symbol_graph_data.smells:
            for smell in symbol_graph_data.smells:
                for frame in all_frames:
                    if not frame.file_path:
                        continue
                    frame_rel = self._make_relative(frame.file_path)
                    smell_rel = self._make_relative(smell.file_path)

                    if frame_rel == smell_rel and frame.line_number is not None and abs(smell.line_number - frame.line_number) <= 3:
                        if smell.smell_type == "circular_dependency":
                            hypotheses.append(Hypothesis(
                                type="import_failure",
                                root_cause=smell.message,
                                origin_file=smell_rel,
                                line_number=smell.line_number,
                                probable_impacted_modules=[],
                                reasoning_summary=f"A circular dependency was detected in '{smell_rel}' near the execution frame at line {frame.line_number}. Circular dependencies can lead to partial module load failures and attribute errors at runtime.",
                                safest_fix_direction="Break the import cycle by refactoring imports, or extract shared dependencies into a separate module.",
                                confidence_score=0.85
                            ))
                        elif smell.smell_type == "broken_reference":
                            hypotheses.append(Hypothesis(
                                type="broken_reference",
                                root_cause=smell.message,
                                origin_file=smell_rel,
                                line_number=smell.line_number,
                                probable_impacted_modules=[frame.module_name] if frame.module_name else [],
                                reasoning_summary=f"An undefined or unimported reference was detected in '{smell_rel}' near the execution frame at line {frame.line_number}.",
                                safest_fix_direction="Import the referenced symbol or verify its declaration scope.",
                                confidence_score=0.90
                            ))
                        elif smell.smell_type == "null_dereference":
                            hypotheses.append(Hypothesis(
                                type="null_reference",
                                root_cause=smell.message,
                                origin_file=smell_rel,
                                line_number=smell.line_number,
                                probable_impacted_modules=[frame.module_name] if frame.module_name else [],
                                reasoning_summary=f"A potential dereference of a null/None object was detected in '{smell_rel}' near line {frame.line_number}.",
                                safest_fix_direction="Insert a safety check (e.g. optional chaining '?.' or 'if obj is not None:') before accessing members.",
                                confidence_score=0.85
                            ))
                        elif smell.smell_type == "missing_guard":
                            is_division = "division" in smell.message.lower() or "divide" in smell.message.lower()
                            h_type = "bad_state_propagation" if is_division else "api_mismatch"
                            rec_action = "Add a zero-check guard on the divisor before division." if is_division else "Add bounds checking on the array/dict index before access."

                            hypotheses.append(Hypothesis(
                                type=h_type,
                                root_cause=smell.message,
                                origin_file=smell_rel,
                                line_number=smell.line_number,
                                probable_impacted_modules=[frame.module_name] if frame.module_name else [],
                                reasoning_summary=f"A missing guard condition check was detected in '{smell_rel}' near the execution frame at line {frame.line_number}.",
                                safest_fix_direction=rec_action,
                                confidence_score=0.80
                            ))

        # 4. Error Signature Matching
        err_type = error.error_type.lower()
        err_msg = error.message.lower()

        crash_file = self._make_relative(surfaced_crash_frame.file_path) if surfaced_crash_frame else ""
        crash_line = surfaced_crash_frame.line_number if surfaced_crash_frame else None
        crash_module = surfaced_crash_frame.module_name if surfaced_crash_frame else None

        if "zerodivisionerror" in err_type or "divisionbyzero" in err_type or "division by zero" in err_msg:
            hypotheses.append(Hypothesis(
                type="bad_state_propagation",
                root_cause="A division by zero was executed at runtime.",
                origin_file=crash_file,
                line_number=crash_line,
                probable_impacted_modules=[crash_module] if crash_module else [],
                reasoning_summary=f"The runtime environment threw a division by zero error at {crash_file}:{crash_line}. Message: '{error.message}'",
                safest_fix_direction="Check the denominator value before division and handle the zero case appropriately.",
                confidence_score=0.90
            ))
        elif "typeerror" in err_type or "nullpointerexception" in err_type or "attributeerror" in err_type:
            is_null_ref = any(pat in err_msg for pat in ("none", "null", "undefined", "nonetype"))
            if is_null_ref:
                hypotheses.append(Hypothesis(
                    type="null_reference",
                    root_cause=f"Dereference of a null or undefined object: {error.message}",
                    origin_file=crash_file,
                    line_number=crash_line,
                    probable_impacted_modules=[crash_module] if crash_module else [],
                    reasoning_summary=f"A TypeError or AttributeError occurred because a None, null, or undefined object was dereferenced at {crash_file}:{crash_line}.",
                    safest_fix_direction="Ensure the object is properly instantiated and initialized, or wrap the dereference with a safety guard.",
                    confidence_score=0.90
                ))
            else:
                hypotheses.append(Hypothesis(
                    type="api_mismatch",
                    root_cause=f"Type mismatch or invalid attribute access: {error.message}",
                    origin_file=crash_file,
                    line_number=crash_line,
                    probable_impacted_modules=[crash_module] if crash_module else [],
                    reasoning_summary=f"A Type or Attribute error occurred at {crash_file}:{crash_line}. Message: '{error.message}'",
                    safest_fix_direction="Inspect expected function argument types or response shapes.",
                    confidence_score=0.80
                ))
        elif "nameerror" in err_type or "referenceerror" in err_type:
            hypotheses.append(Hypothesis(
                type="broken_reference",
                root_cause=f"Undefined variable or name: {error.message}",
                origin_file=crash_file,
                line_number=crash_line,
                probable_impacted_modules=[crash_module] if crash_module else [],
                reasoning_summary=f"The runtime encountered an undefined variable or property identifier at {crash_file}:{crash_line}.",
                safest_fix_direction="Verify that the variable or function is defined, imported, or correctly spelled.",
                confidence_score=0.90
            ))
        elif any(x in err_type for x in ["keyerror", "indexerror", "rangeerror", "valueerror", "lookuperror"]):
            hypotheses.append(Hypothesis(
                type="bad_state_propagation",
                root_cause=f"Runtime error due to invalid state or key/index lookup: {error.message}",
                origin_file=crash_file,
                line_number=crash_line,
                probable_impacted_modules=[crash_module] if crash_module else [],
                reasoning_summary=f"The application encountered an invalid lookup or value restriction at {crash_file}:{crash_line}.",
                safest_fix_direction="Check that keys, indices, and ranges are validated before access.",
                confidence_score=0.85
            ))
        elif any(x in err_type for x in ["clierror", "error"]):
            hypotheses.append(Hypothesis(
                type="bad_state_propagation",
                root_cause=f"Compiler or CLI command failure: {error.message}",
                origin_file=crash_file,
                line_number=crash_line,
                probable_impacted_modules=[crash_module] if crash_module else [],
                reasoning_summary=f"A compiler error or command line error was output during execution: '{error.message}'",
                safest_fix_direction="Check for syntax errors, missing declarations, or invalid command arguments.",
                confidence_score=0.85
            ))

        # 5. Timing / Async issues
        is_js_ts = error.language.lower() in ("javascript", "typescript")
        if is_js_ts:
            has_async_frames = False
            async_keywords = {"promise", "async", "await", "anonymous", "next", "generator", "microtasks", "processtickstickandrejections"}
            for frame in all_frames:
                func_name = (frame.function_name or "").lower()
                context = (frame.code_context or frame.raw_line or "").lower()
                if any(kw in func_name for kw in async_keywords) or "await" in context or "async" in context:
                    has_async_frames = True
                    break

            if has_async_frames:
                hypotheses.append(Hypothesis(
                    type="async_issue",
                    root_cause="Possible JavaScript/TypeScript async timing issue or unhandled Promise rejection.",
                    origin_file=crash_file,
                    line_number=crash_line,
                    probable_impacted_modules=[crash_module] if crash_module else [],
                    reasoning_summary="The call stack contains asynchronous frames, Promise references, or async/await wrappers, indicating the crash occurred within an asynchronous execution path.",
                    safest_fix_direction="Verify that all promises in this execution chain are properly awaited and that error handlers (try/catch or .catch()) are registered.",
                    confidence_score=0.75
                ))

        # Compile propagation chain
        propagation_chain = []
        for chained in error.chained_errors:
            for frame in chained.frames:
                if frame.file_path:
                    func = frame.function_name or "anonymous"
                    propagation_chain.append(f"{self._make_relative(frame.file_path)}:{frame.line_number} in {func}")

        for frame in error.frames:
            if frame.file_path:
                func = frame.function_name or "anonymous"
                propagation_chain.append(f"{self._make_relative(frame.file_path)}:{frame.line_number} in {func}")

        # De-duplicate hypotheses
        seen = {}
        for h in hypotheses:
            key = (h.type, h.origin_file, h.line_number)
            if key not in seen or h.confidence_score > seen[key].confidence_score:
                seen[key] = h

        sorted_hypotheses = sorted(seen.values(), key=lambda h: h.confidence_score, reverse=True)

        if not sorted_hypotheses:
            sorted_hypotheses.append(Hypothesis(
                type="generic",
                root_cause="Unresolved runtime execution failure.",
                origin_file=crash_file,
                line_number=crash_line,
                probable_impacted_modules=[crash_module] if crash_module else [],
                reasoning_summary="No specific static analysis triggers, code smells, or environment mismatches matched the runtime traceback pattern.",
                safest_fix_direction="Perform runtime inspection or check local variables at the crash frame.",
                confidence_score=0.30
            ))

        return RCAResult(
            hypotheses=sorted_hypotheses,
            propagation_chain=propagation_chain
        )
