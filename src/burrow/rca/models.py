from typing import List, Optional
from pydantic import BaseModel, Field

class Hypothesis(BaseModel):
    type: str  # config_issue, env_mismatch, import_failure, async_issue, bad_state_propagation, null_reference, api_mismatch, recent_change, generic
    root_cause: str
    origin_file: str
    line_number: Optional[int] = None
    probable_impacted_modules: List[str] = Field(default_factory=list)
    reasoning_summary: str
    safest_fix_direction: str
    confidence_score: float

class RCAResult(BaseModel):
    hypotheses: List[Hypothesis] = Field(default_factory=list)
    propagation_chain: List[str] = Field(default_factory=list)
