from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class SymbolType(str, Enum):
    FUNCTION = "FUNCTION"
    CLASS = "CLASS"
    METHOD = "METHOD"
    VARIABLE = "VARIABLE"
    MODULE = "MODULE"

class SymbolDetail(BaseModel):
    name: str
    type: SymbolType
    file_path: str
    start_line: int
    end_line: int
    docstring: Optional[str] = None
    signature: Optional[str] = None

class CallSiteDetail(BaseModel):
    caller: Optional[str] = None  # Function/Method name containing the call
    callee: str
    file_path: str
    line_number: int
    is_method_call: bool = False

class CodeSmell(BaseModel):
    smell_type: str  # circular_dependency, null_dereference, broken_reference, missing_guard, loose_comparison
    message: str
    file_path: str
    line_number: int
    severity: str  # error, warning, info

class SymbolGraphData(BaseModel):
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)
    smells: List[CodeSmell] = Field(default_factory=list)
