import re
from pathlib import Path
from typing import Optional, List
from burrow.parser.models import NormalizedError
from burrow.workspace.models import WorkspaceContext
from burrow.symbol.models import SymbolGraphData
from burrow.rca.models import RCAResult
from burrow.llm.base import LLMRecommendation
from burrow.remediation.models import FixSuggestion, RemediationResult

class RemediationEngine:
    """Remediation & Patch suggestion engine that coordinates static heuristics and AI recommendations."""
    
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        
    def generate_suggestions(
        self,
        error: NormalizedError,
        workspace_context: Optional[WorkspaceContext] = None,
        symbol_graph_data: Optional[SymbolGraphData] = None,
        rca_result: Optional[RCAResult] = None,
        recommendation: Optional[LLMRecommendation] = None,
    ) -> RemediationResult:
        suggestions = []
        
        # 1. Config / Env Fixes (High confidence, very safe)
        if rca_result:
            for hyp in rca_result.hypotheses:
                if hyp.type == "config_issue":
                    suggestions.append(FixSuggestion(
                        description="Create local environment configuration file from template",
                        affected_file=".env",
                        likely_edit_region="Entire file",
                        rationale="A local `.env` configuration file was not found, but `.env.example` exists. Creating `.env` ensures variables are present.",
                        risk_level="safe",
                        patch_preview="Create new file '.env' and populate with keys from '.env.example'",
                        confidence_score=0.95
                    ))
                elif hyp.type == "env_mismatch":
                    # Try to extract the key from hyp.root_cause
                    missing_key = "VARIABLE_NAME"
                    parts = hyp.root_cause.split(":")
                    if len(parts) > 1:
                        missing_key = parts[1].strip()
                    else:
                        # Fallback parsing
                        match = re.search(r"'(.*?)'", hyp.root_cause)
                        if match:
                            missing_key = match.group(1)
                            
                    suggestions.append(FixSuggestion(
                        description=f"Define missing environment variable '{missing_key}' in `.env`",
                        affected_file=".env",
                        likely_edit_region="Append to file",
                        rationale=f"The application expects environment variable '{missing_key}' to be loaded, but it is currently undefined locally.",
                        risk_level="safe",
                        patch_preview=f"+ {missing_key}=value_here",
                        confidence_score=0.95
                    ))
        
        # 1.1 Env Loading Order Fixes
        if rca_result and any(hyp.type in ("config_issue", "env_mismatch") for hyp in rca_result.hypotheses):
            entrypoint = "app.py"
            if workspace_context and workspace_context.structure and workspace_context.structure.entrypoints:
                entrypoint = workspace_context.structure.entrypoints[0]
            suggestions.append(FixSuggestion(
                description="Ensure environment variables are loaded at the absolute entrypoint",
                affected_file=entrypoint,
                likely_edit_region="First lines of file",
                rationale="Ensures environment variables are fully populated in the process before other modules attempt to access them during import.",
                risk_level="safe",
                patch_preview="[Python Example]\nimport dotenv\ndotenv.load_dotenv()\n# Import other dependencies after loading env",
                confidence_score=0.95
            ))
 
        # 2. Null Guards & Code Smell Fixes
        null_smells = []
        if symbol_graph_data and symbol_graph_data.smells:
            null_smells = [s for s in symbol_graph_data.smells if s.smell_type in ("null_dereference", "missing_guard")]
            
        for smell in null_smells:
            suggestions.append(FixSuggestion(
                description=f"Add defensive null guard or zero/empty check for variable before access in {smell.file_path}",
                affected_file=smell.file_path,
                likely_edit_region=f"Line {smell.line_number}",
                rationale=f"Prevents failures like undefined attribute or null dereference accesses. Smell message: {smell.message}",
                risk_level="safe",
                patch_preview=f"if variable is not None:\n    # access attribute safely",
                confidence_score=0.90
            ))
 
        # 3. Defensive Checks based on Error Signatures
        err_type_lower = error.error_type.lower()
        if "zerodivision" in err_type_lower:
            crash_file = "app.py"
            crash_line = 1
            if error.surfaced_crash_point:
                crash_file = error.surfaced_crash_point.file_path
                crash_line = error.surfaced_crash_point.line_number
            elif error.frames:
                crash_file = error.frames[-1].file_path
                crash_line = error.frames[-1].line_number
                
            suggestions.append(FixSuggestion(
                description="Introduce non-zero divisor guard/check before division/modulo operation",
                affected_file=crash_file,
                likely_edit_region=f"Line {crash_line}",
                rationale="Avoids runtime ZeroDivisionError by ensuring division is only performed when the denominator is not zero.",
                risk_level="safe",
                patch_preview="if divisor == 0:\n    # handle default case or raise custom exception\nelse:\n    result = value / divisor",
                confidence_score=0.85
            ))
            
        elif "typeerror" in err_type_lower:
            crash_file = "app.py"
            crash_line = 1
            if error.surfaced_crash_point:
                crash_file = error.surfaced_crash_point.file_path
                crash_line = error.surfaced_crash_point.line_number
            elif error.frames:
                crash_file = error.frames[-1].file_path
                crash_line = error.frames[-1].line_number
            suggestions.append(FixSuggestion(
                description="Add runtime type assertions or explicit coercion",
                affected_file=crash_file,
                likely_edit_region=f"Line {crash_line}",
                rationale="Ensures that variable type is compatible with the operation (e.g. converting a string to an integer before addition).",
                risk_level="safe",
                patch_preview="if not isinstance(variable, expected_type):\n    variable = coerce_type(variable)",
                confidence_score=0.85
            ))
 
        # 4. Import Corrections & Dependency Updates
        if rca_result:
            for hyp in rca_result.hypotheses:
                if hyp.type == "import_failure":
                    suggestions.append(FixSuggestion(
                        description="Verify import path and package spelling",
                        affected_file=hyp.origin_file,
                        likely_edit_region=f"Line {hyp.line_number or 1}",
                        rationale="Resolves import failures by ensuring the correct local module or file is imported.",
                        risk_level="safe",
                        patch_preview=None,
                        confidence_score=0.80
                    ))
                    
                    pkg_file = "requirements.txt"
                    if workspace_context and workspace_context.structure:
                        struct = workspace_context.structure
                        if "npm" in struct.package_managers:
                            pkg_file = "package.json"
                            
                    suggestions.append(FixSuggestion(
                        description=f"Install or update project dependencies in {pkg_file}",
                        affected_file=pkg_file,
                        likely_edit_region="Dependency list",
                        rationale="Ensures the required library is installed and available in the execution environment.",
                        risk_level="medium",
                        patch_preview=f"+ \"package_name\": \"^version\"",
                        confidence_score=0.70
                    ))
 
        # 5. Async Timing / Retry Logic
        has_async = False
        if rca_result:
            for hyp in rca_result.hypotheses:
                if hyp.type == "async_issue":
                    has_async = True
                    break
        if has_async:
            crash_file = "app.py"
            crash_line = 1
            if error.surfaced_crash_point:
                crash_file = error.surfaced_crash_point.file_path
                crash_line = error.surfaced_crash_point.line_number
            elif error.frames:
                crash_file = error.frames[-1].file_path
                crash_line = error.frames[-1].line_number
            suggestions.append(FixSuggestion(
                description="Wrap call in a retry logic helper or add defensive awaiting/synchronization",
                affected_file=crash_file,
                likely_edit_region=f"Line {crash_line}",
                rationale="Mitigates race conditions or transient timing failures in asynchronous functions by retrying on failure or using synchronization locks.",
                risk_level="medium",
                patch_preview="for attempt in range(max_retries):\n    try:\n        return await async_call()\n    except TransientError:\n        await asyncio.sleep(backoff)",
                confidence_score=0.70
            ))
 
        # 6. AI-driven structural patch suggestions (if recommendation is present)
        if recommendation:
            code_blocks = re.findall(r"```(?:\w+)?\n(.*?)\n```", recommendation.remediation, re.DOTALL)
            
            ai_description = recommendation.remediation
            if len(ai_description) > 200:
                ai_description = ai_description[:200] + "..."
                
            ai_patch = code_blocks[0] if code_blocks else None
            
            ai_files = recommendation.related_files if recommendation.related_files else ["Multiple Files"]
            for file_path in ai_files[:3]:
                # Heuristically score AI suggestions based on recommendation confidence
                ai_conf = max(0.40, recommendation.confidence * 0.8)
                suggestions.append(FixSuggestion(
                    description="Apply AI-suggested structural remediation",
                    affected_file=file_path,
                    likely_edit_region="Identified edit region",
                    rationale=recommendation.cause,
                    risk_level="risky" if ai_patch else "medium",
                    patch_preview=ai_patch or recommendation.remediation,
                    confidence_score=ai_conf
                ))
 
        # Sort suggestions: safe (1) -> medium (2) -> risky (3)
        def risk_score(s: FixSuggestion) -> int:
            if s.risk_level == "safe":
                return 1
            elif s.risk_level == "medium":
                return 2
            else:
                return 3
                
        suggestions.sort(key=risk_score)
        
        # De-duplicate suggestions
        seen = set()
        unique_suggestions = []
        for s in suggestions:
            key = (s.description, s.affected_file, s.risk_level)
            if key not in seen:
                seen.add(key)
                unique_suggestions.append(s)

        for s in unique_suggestions:
            s.original_sha256 = self._compute_sha256(s.affected_file)
                
        return RemediationResult(suggestions=unique_suggestions)

    def _compute_sha256(self, file_path: str) -> Optional[str]:
        """Computes SHA-256 checksum of file relative to project root."""
        import hashlib
        try:
            # First handle special case like .env or relative files
            resolved = Path(self.project_root / file_path).resolve()
            # Safety check: make sure resolved is relative to project_root to prevent traversal
            if not resolved.is_relative_to(self.project_root):
                return None
            if not resolved.exists() or not resolved.is_file():
                return None
            hasher = hashlib.sha256()
            with open(resolved, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return None
