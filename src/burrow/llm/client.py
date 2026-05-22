from burrow.llm.base import BaseLLMClient, LLMRecommendation
from burrow.parser.models import NormalizedError

class MockLLMClient(BaseLLMClient):
    """Deterministic Mock LLM integration that provides static recommendations based on exception patterns."""
    
    def analyze_error(self, error: NormalizedError) -> LLMRecommendation:
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
    """Placeholder client representing a future local model integrations (e.g. Ollama)."""
    
    def __init__(self, endpoint: str = "http://localhost:11434"):
        self.endpoint = endpoint
        
    def analyze_error(self, error: NormalizedError) -> LLMRecommendation:
        # Falls back to Mock behavior for Stage 1 setup validation
        return MockLLMClient().analyze_error(error)


def get_llm_client(provider: str = "mock") -> BaseLLMClient:
    """Factory function returning the configured LLM client."""
    if provider.lower() == "ollama":
        return LocalOllamaClient()
    return MockLLMClient()
