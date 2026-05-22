from typing import List, Optional
from pydantic import BaseModel, Field

class FixSuggestion(BaseModel):
    description: str
    affected_file: str
    likely_edit_region: Optional[str] = None
    rationale: str
    risk_level: str = "safe"  # safe, medium, risky
    patch_preview: Optional[str] = None
    confidence_score: float = 0.50

class RemediationResult(BaseModel):
    suggestions: List[FixSuggestion] = Field(default_factory=list)
