from abc import ABC, abstractmethod
from typing import List
from pydantic import BaseModel, Field
from burrow.parser.models import NormalizedError

class LLMRecommendation(BaseModel):
    """Structured response from the LLM engine for debugging suggestions."""
    cause: str
    remediation: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    related_files: List[str] = Field(default_factory=list)


class BaseLLMClient(ABC):
    """Abstract base class for all LLM service integrations."""
    
    @abstractmethod
    def analyze_error(self, error: NormalizedError) -> LLMRecommendation:
        """Sends NormalizedError and source contexts to LLM, returning a structured remediation recommendation."""
        pass
