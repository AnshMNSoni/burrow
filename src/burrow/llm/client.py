import json
import urllib.request
import urllib.error
from typing import Optional, List
from burrow.utils.logging import logger
from burrow.llm.base import BaseLLMClient, LLMRecommendation
from burrow.parser.models import NormalizedError
from burrow.workspace.models import WorkspaceContext
from burrow.symbol.models import SymbolGraphData
from burrow.rca.models import RCAResult

class MockLLMClient(BaseLLMClient):
    """Deterministic Mock LLM integration that provides static recommendations based on exception patterns."""
    
    def analyze_error(
        self,
        error: NormalizedError,
        workspace_context: Optional[WorkspaceContext] = None,
        symbol_graph_data: Optional[SymbolGraphData] = None,
        rca_result: Optional[RCAResult] = None,
    ) -> LLMRecommendation:
        # Check files referenced in frames
        related = list(set(frame.file_path for frame in error.frames if frame.is_application_code))
        
        err_type = error.error_type.lower()
        
        if "zerodivision" in err_type:
            return LLMRecommendation(
                cause="A division or modulo operation was executed with a denominator of zero.",
                remediation="Add a guard condition to ensure the divisor is non-zero, or use a default fallback value if the divisor is zero.",
                confidence=0.95,
                related_files=related
            )
        elif "typeerror" in err_type:
            return LLMRecommendation(
                cause=f"An operation or method was invoked on an incompatible object. Details: {error.message}",
                remediation="Inspect variables to ensure correct data types. Use static type hints or runtime isinstance/typeof checks before operation.",
                confidence=0.85,
                related_files=related
            )
        elif "keyerror" in err_type:
            return LLMRecommendation(
                cause=f"Attempted to access a dictionary/map key that does not exist in the collection. Missing key: {error.message}",
                remediation="Check if the key exists using 'key in dict' or fetch values safely via the 'dict.get(key, default)' method.",
                confidence=0.90,
                related_files=related
            )
        elif "filenotfound" in err_type:
            return LLMRecommendation(
                cause=f"The operating system could not find the file or directory specified. Path details: {error.message}",
                remediation="Verify the target filepath exists, check path permissions, or use Path.exists() before invoking open operations.",
                confidence=0.90,
                related_files=related
            )
        else:
            return LLMRecommendation(
                cause=f"An unhandled exception of type '{error.error_type}' was raised with message: '{error.message}'.",
                remediation="Examine the code snippet context surrounding the crash line. Inspect call stack values to locate anomalous states.",
                confidence=0.70,
                related_files=related
            )


class LocalOllamaClient(BaseLLMClient):
    """Client for local Ollama service integration."""
    
    def __init__(self, endpoint: Optional[str] = None, model: Optional[str] = None, timeout: float = 10.0):
        from burrow.config import settings
        self.endpoint = endpoint or getattr(settings, "ollama_endpoint", "http://localhost:11434")
        self.model = model or getattr(settings, "ollama_model", "qwen2.5-coder")
        self.timeout = timeout
        
    def build_prompt(
        self,
        error: NormalizedError,
        workspace_context: Optional[WorkspaceContext] = None,
        symbol_graph_data: Optional[SymbolGraphData] = None,
        rca_result: Optional[RCAResult] = None,
    ) -> str:
        prompt_lines = [
            "You are Burrow's local AI reasoning agent, a specialized coding assistant that analyzes runtime errors, source code context, and static analysis outputs to find root causes and suggest remediation actions.",
            "Your task is to analyze the provided debug context and determine the underlying root cause of the error. Then, suggest a safe remediation.",
            "",
            "CRITICAL: You MUST respond with a single, valid JSON object matching the JSON schema below.",
            "Do NOT wrap the output in markdown code blocks (like ```json) or include any extra text. Return ONLY the raw JSON object.",
            "",
            "JSON SCHEMA:",
            "{",
            '  "cause": "A concise explanation of the root cause of the failure.",',
            '  "remediation": "Actionable and safe step-by-step instructions or code to fix the issue.",',
            '  "confidence": 0.85, // Float between 0.0 and 1.0 representing confidence in the explanation.',
            '  "related_files": ["list", "of", "impacted/related", "files"]',
            "}",
            "",
            "DEBUG CONTEXT:",
            "========================================",
            f"Error Type: {error.error_type}",
            f"Error Message: {error.message}",
            f"Language: {error.language}",
            "",
        ]

        # Stack Trace Frames
        prompt_lines.append("STACK TRACE FRAMES:")
        for idx, frame in enumerate(error.frames):
            app_tag = " [Application Code]" if frame.is_application_code else ""
            prompt_lines.append(f"- Frame #{idx + 1}: {frame.file_path}:{frame.line_number or '?'} in function `{frame.function_name or '?'}`{app_tag}")
        prompt_lines.append("")

        # Chained Errors
        if error.chained_errors:
            prompt_lines.append("CHAINED ERRORS:")
            for idx, chained in enumerate(error.chained_errors):
                prompt_lines.append(f"- Chained #{idx + 1}: {chained.error_type} - {chained.message}")
            prompt_lines.append("")

        # Code Context
        prompt_lines.append("NEARBY SOURCE CODE CONTEXT:")
        context_count = 0
        for frame in reversed(error.frames):
            if frame.code_context and context_count < 5:
                prompt_lines.append(f"File: {frame.file_path}:{frame.line_number or '?'}")
                prompt_lines.append("```")
                prompt_lines.append(frame.code_context)
                prompt_lines.append("```")
                prompt_lines.append("")
                context_count += 1

        # Detected Anomalies / Hypotheses from RCA engine
        if rca_result and rca_result.hypotheses:
            prompt_lines.append("STATIC RCA ENGINE HYPOTHESES:")
            for idx, hyp in enumerate(rca_result.hypotheses[:5]):
                prompt_lines.append(
                    f"- Hypothesis #{idx + 1} [Confidence: {hyp.confidence_score * 100:.1f}%]:\n"
                    f"  Type: {hyp.type}\n"
                    f"  Root Cause: {hyp.root_cause}\n"
                    f"  Reasoning: {hyp.reasoning_summary}\n"
                    f"  Suggested Fix: {hyp.safest_fix_direction}"
                )
            prompt_lines.append("")

        # Propagation Chain
        if rca_result and rca_result.propagation_chain:
            chain_str = " -> ".join(rca_result.propagation_chain)
            prompt_lines.append(f"EXECUTION PROPAGATION CHAIN:\n{chain_str}")
            prompt_lines.append("")

        # Code Smells
        if symbol_graph_data and symbol_graph_data.smells:
            prompt_lines.append("SYMBOL GRAPH DETECTED CODE SMELLS:")
            for smell in symbol_graph_data.smells[:10]:
                prompt_lines.append(f"- [{smell.smell_type.upper()}] {smell.file_path}:{smell.line_number} - {smell.message} (Severity: {smell.severity})")
            prompt_lines.append("")

        # Workspace Context
        if workspace_context:
            prompt_lines.append("WORKSPACE METADATA:")
            if workspace_context.structure:
                struct = workspace_context.structure
                if struct.detected_frameworks:
                    prompt_lines.append(f"- Detected Frameworks: {', '.join(struct.detected_frameworks)}")
                if struct.package_managers:
                    prompt_lines.append(f"- Package Managers: {', '.join(struct.package_managers)}")
            
            # Dependencies
            if workspace_context.dependencies:
                deps_list = []
                for dep_type, dep_list in workspace_context.dependencies.items():
                    prod_deps = [d for d in dep_list if d.scope == "production"][:10]
                    if prod_deps:
                        deps_list.append(f"{dep_type}: " + ", ".join(f"{d.name} ({d.version or 'any'})" for d in prod_deps))
                if deps_list:
                    prompt_lines.append("- Key Dependencies:")
                    for dep in deps_list:
                        prompt_lines.append(f"  {dep}")
            
            # Git Status
            if workspace_context.git and workspace_context.git.recent_changes:
                recent_changes = [f"{c.file_path} ({c.status})" for c in workspace_context.git.recent_changes[:10]]
                prompt_lines.append(f"- Modified/Untracked Git Files: {', '.join(recent_changes)}")
            prompt_lines.append("")

        prompt_lines.append("========================================")
        prompt_lines.append("Generate only the JSON object now. Do not include markdown code block syntax.")
        return "\n".join(prompt_lines)

    def analyze_error(
        self,
        error: NormalizedError,
        workspace_context: Optional[WorkspaceContext] = None,
        symbol_graph_data: Optional[SymbolGraphData] = None,
        rca_result: Optional[RCAResult] = None,
    ) -> LLMRecommendation:
        prompt = self.build_prompt(error, workspace_context, symbol_graph_data, rca_result)
        
        url = f"{self.endpoint.rstrip('/')}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 512
            }
        }
        
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                resp_data = json.loads(response.read().decode("utf-8"))
                response_text = resp_data.get("response", "").strip()
                
            # Now parse the JSON response text
            parsed_recommendation = json.loads(response_text)
            
            # Extract fields and validate
            cause = parsed_recommendation.get("cause")
            remediation = parsed_recommendation.get("remediation")
            confidence = parsed_recommendation.get("confidence")
            related_files = parsed_recommendation.get("related_files")
            
            # Basic validation
            if not cause or not remediation:
                raise ValueError("Ollama response missing required 'cause' or 'remediation' fields.")
                
            # Ensure types are correct
            if not isinstance(confidence, (int, float)):
                confidence = 0.8  # default if not float
            else:
                confidence = max(0.0, min(1.0, float(confidence)))
                
            if not isinstance(related_files, list):
                related_files = [str(related_files)] if related_files else []
            else:
                related_files = [str(r) for r in related_files if r]
                
            return LLMRecommendation(
                cause=str(cause),
                remediation=str(remediation),
                confidence=confidence,
                related_files=related_files
            )
        except Exception as e:
            logger.warning(f"Ollama inference or parsing failed: {e}. Falling back to static recommendation engine.")
            return MockLLMClient().analyze_error(error, workspace_context, symbol_graph_data, rca_result)


def get_llm_client(provider: str = "mock") -> BaseLLMClient:
    """Factory function returning the configured LLM client."""
    if provider.lower() == "ollama":
        return LocalOllamaClient()
    return MockLLMClient()
