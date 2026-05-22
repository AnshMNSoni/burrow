from abc import ABC, abstractmethod
from burrow.parser.models import NormalizedError

class BaseAnalyzer(ABC):
    """Abstract base class for all context analyzers."""
    
    @abstractmethod
    def analyze(self, error: NormalizedError) -> None:
        """Enrich the NormalizedError with additional diagnostic context."""
        pass
