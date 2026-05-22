from pathlib import Path
from typing import Dict, Any, Optional
from pydantic import BaseModel
from burrow.config import settings
from burrow.parser import LogParser, NormalizedError
from burrow.analyzer import LocalSourceAnalyzer
from burrow.graph import Graph, populate_error_graph
from burrow.llm import get_llm_client, LLMRecommendation
from burrow.workspace.scanner import WorkspaceScanner
from burrow.workspace.models import WorkspaceContext
from burrow.utils.logging import logger

class AnalysisResult(BaseModel):
    """Encapsulates the complete result of a traceback analysis session."""
    error: NormalizedError
    recommendation: LLMRecommendation
    graph: Dict[str, Any]
    workspace_context: Optional[WorkspaceContext] = None


class BurrowEngine:
    """Core coordinating engine for the Burrow debugging intelligence system."""
    
    def __init__(self, project_root: Optional[Path] = None, llm_provider: Optional[str] = None):
        self.project_root = Path(project_root or settings.project_root).resolve()
        self.llm_provider = llm_provider or settings.llm_provider
        self.analyzer = LocalSourceAnalyzer(self.project_root)
        self.workspace_scanner = WorkspaceScanner(self.project_root)
        self.llm_client = get_llm_client(self.llm_provider)

    def analyze_content(self, content: str) -> AnalysisResult:
        """Processes raw trace text, runs parsing, static source context indexing, relation graphing, and LLM mapping."""
        logger.info("Initializing traceback analysis sequence.")
        
        # 1. Ingest & Parse
        parser = LogParser()
        if not parser.can_parse(content):
            logger.warning("Input content structure not recognized; attempting parsing with fallback strategies.")
            
        error = parser.parse(content)
        logger.info(f"Traceback parsed successfully. Language: {error.language}. Type: {error.error_type}")

        # 2. Extract context from source code files
        logger.info("Starting local file code snippet indexing...")
        self.analyzer.analyze(error)

        # 3. Form call graph relations
        logger.info("Compiling propagation dependency graph...")
        graph = Graph()
        populate_error_graph(graph, error)

        # 4. Run workspace intelligence scan
        logger.info("Running workspace intelligence scan...")
        workspace_context = None
        try:
            workspace_context = self.workspace_scanner.scan()
        except Exception as e:
            logger.error(f"Workspace scan failed: {e}")

        # 5. Correlate traceback frames with Git status
        if workspace_context and workspace_context.git:
            git_status_map = {change.file_path: change.status for change in workspace_context.git.recent_changes}
            
            def annotate_frames(err):
                for frame in err.frames:
                    if not frame.file_path:
                        continue
                    try:
                        resolved_frame_path = Path(frame.file_path).resolve()
                        rel_path = resolved_frame_path.relative_to(self.project_root).as_posix()
                        if rel_path in git_status_map:
                            frame.metadata["git_status"] = git_status_map[rel_path]
                    except Exception:
                        pass
                for chained in err.chained_errors:
                    annotate_frames(chained)
            
            annotate_frames(error)

        # 6. Compile AI suggestions
        logger.info(f"Querying reasoning intelligence (provider: {self.llm_provider})...")
        recommendation = self.llm_client.analyze_error(error)

        logger.info("Traceback analysis sequence completed successfully.")
        return AnalysisResult(
            error=error,
            recommendation=recommendation,
            graph=graph.to_dict(),
            workspace_context=workspace_context
        )

    def analyze_file(self, file_path: Path) -> AnalysisResult:
        """Reads trace file and performs analysis."""
        resolved_path = Path(file_path).resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"Traceback source file not found: {resolved_path}")
            
        logger.info(f"Reading target traceback file: {resolved_path}")
        with open(resolved_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            
        return self.analyze_content(content)
