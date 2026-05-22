import uuid
import time
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

class RuntimeEvent(BaseModel):
    """Represents a diagnostic or failure event captured during runtime monitoring."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    source: str  # e.g., 'python', 'pytest', 'npm', 'docker', 'generic'
    timestamp: float = Field(default_factory=time.time)
    content: str  # raw traceback or log line block
    exit_code: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class SessionState(BaseModel):
    """Tracks state and events within a single watch/run monitoring session."""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    start_time: float = Field(default_factory=time.time)
    events: List[RuntimeEvent] = Field(default_factory=list)
