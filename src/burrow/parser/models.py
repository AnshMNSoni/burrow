from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class NormalizedFrame(BaseModel):
    file_path: str
    line_number: Optional[int] = None
    column_number: Optional[int] = None
    function_name: Optional[str] = None
    module_name: Optional[str] = None
    code_context: Optional[str] = None
    raw_line: str
    is_application_code: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)

class NormalizedError(BaseModel):
    error_type: str
    message: str
    frames: List[NormalizedFrame] = Field(default_factory=list)
    language: str = "unknown"
    raw_input: str
    root_origin: Optional[NormalizedFrame] = None
    surfaced_crash_point: Optional[NormalizedFrame] = None
    confidence_score: float = 1.0
    chained_errors: List["NormalizedError"] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

# Rebuild self-referential model for Pydantic v2
NormalizedError.model_rebuild()

