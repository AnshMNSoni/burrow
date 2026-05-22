from abc import ABC, abstractmethod
from typing import Optional
from burrow.parser.models import NormalizedError, NormalizedFrame

class ParsingError(Exception):
    """Raised when parsing fails."""
    pass

class BaseParser(ABC):
    """Abstract base class for trace and log parsers."""
    
    # Python displays trace from oldest to newest (crash is last).
    # JS displays trace from newest to oldest (crash is first).
    crash_on_last_frame: bool = True
    
    @abstractmethod
    def can_parse(self, content: str) -> bool:
        """Return True if this parser can handle the content."""
        pass

    @abstractmethod
    def parse(self, content: str) -> NormalizedError:
        """Parse the raw content and return a NormalizedError."""
        pass

    def get_surfaced_crash_point(self, error: NormalizedError) -> Optional[NormalizedFrame]:
        """Returns the frame where the failure surfaced."""
        if not error.frames:
            return None
        return error.frames[-1] if self.crash_on_last_frame else error.frames[0]

    def get_root_origin(self, error: NormalizedError) -> Optional[NormalizedFrame]:
        """Locates the deepest application code frame leading to the failure."""
        if not error.frames:
            return None
            
        app_frames = [f for f in error.frames if f.is_application_code]
        if not app_frames:
            # Fallback to surfaced crash point if no app frames are identified
            return self.get_surfaced_crash_point(error)
            
        # For Python (oldest-to-newest), the deepest app frame is the last app frame
        # For JS (newest-to-oldest), the deepest app frame is the first app frame
        return app_frames[-1] if self.crash_on_last_frame else app_frames[0]

    def calculate_confidence(self, error: NormalizedError, content: str) -> float:
        """Heuristically calculates confidence score (0.0 to 1.0) of parser success."""
        if not error.frames:
            return 0.1
            
        score = 1.0
        
        # Generic fallback exception type detection penalization
        generic_types = {"pythonerror", "javascripterror", "genericerror", "unknownerror", "error"}
        if error.error_type.lower() in generic_types:
            score -= 0.25
            
        if not error.message or error.message.strip() == "":
            score -= 0.15
            
        # Frame verification
        unresolved_count = 0
        for frame in error.frames:
            if not frame.file_path or frame.line_number is None:
                unresolved_count += 1
        
        if unresolved_count:
            # Penalize for missing file paths/line numbers in frames
            penalty = min(0.3, unresolved_count * 0.1)
            score -= penalty
            
        return max(0.1, min(1.0, score))

