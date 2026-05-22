import hashlib
import threading
import time
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
from burrow.symbol.models import SymbolGraphData
from burrow.symbol.graph import SymbolGraphBuilder
from burrow.symbol.analyzer import SymbolGraphAnalyzer
from burrow.rca.models import RCAResult
from burrow.rca.engine import RootCauseAnalyzer
from burrow.remediation.models import RemediationResult
from burrow.remediation.engine import RemediationEngine
from burrow.utils.logging import logger


class AnalysisResult(BaseModel):
    """Encapsulates the complete result of a traceback analysis session."""
    error: NormalizedError
    recommendation: LLMRecommendation
    graph: Dict[str, Any]
    workspace_context: Optional[WorkspaceContext] = None
    symbol_graph_data: Optional[SymbolGraphData] = None
    rca_result: Optional[RCAResult] = None
    remediation_result: Optional[RemediationResult] = None


# ── Result Cache ──────────────────────────────────────────────────────────────

class _ResultCache:
    """Thread-safe LRU-like result cache keyed by content hash."""

    def __init__(self, max_size: int = 10, ttl: float = 60.0):
        self._cache: Dict[str, tuple] = {}  # hash -> (AnalysisResult, timestamp)
        self._max_size = max_size
        self._ttl = ttl
        self._lock = threading.Lock()

    def get(self, content_hash: str) -> Optional[AnalysisResult]:
        with self._lock:
            entry = self._cache.get(content_hash)
            if entry is None:
                return None
            result, ts = entry
            if time.monotonic() - ts > self._ttl:
                del self._cache[content_hash]
                return None
            return result

    def put(self, content_hash: str, result: AnalysisResult):
        with self._lock:
            # Evict oldest entries when at capacity
            if len(self._cache) >= self._max_size:
                oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
                del self._cache[oldest_key]
            self._cache[content_hash] = (result, time.monotonic())

    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def clear(self):
        with self._lock:
            self._cache.clear()


# ── Per-root Singleton Registry ───────────────────────────────────────────────

_engine_registry: Dict[str, "BurrowEngine"] = {}
_engine_registry_lock = threading.Lock()


class BurrowEngine:
    """Core coordinating engine for the Burrow debugging intelligence system."""

    def __init__(self, project_root: Optional[Path] = None, llm_provider: Optional[str] = None):
        self.project_root = Path(project_root or settings.project_root).resolve()
        self.llm_provider = llm_provider or settings.llm_provider
        self.analyzer = LocalSourceAnalyzer(self.project_root)
        self.workspace_scanner = WorkspaceScanner(
            self.project_root,
            max_files_to_parse=settings.max_scan_files
        )
        self.llm_client = get_llm_client(self.llm_provider)

        # Per-engine result cache
        self._cache = _ResultCache(
            max_size=settings.cache_size,
            ttl=settings.cache_ttl
        )

        # Workspace scan cache: (WorkspaceContext, last_scan_monotonic)
        self._ws_cache: Optional[tuple] = None
        self._ws_cache_lock = threading.Lock()

    @classmethod
    def get_shared(cls, project_root: Optional[Path] = None, llm_provider: Optional[str] = None) -> "BurrowEngine":
        """Returns a per-root singleton engine. Safe to call from multiple threads."""
        resolved = str(Path(project_root or settings.project_root).resolve())
        with _engine_registry_lock:
            if resolved not in _engine_registry:
                logger.debug(f"Creating new shared BurrowEngine for root: {resolved}")
                _engine_registry[resolved] = cls(project_root=resolved, llm_provider=llm_provider)
            return _engine_registry[resolved]

    def _get_workspace_context(self) -> Optional[WorkspaceContext]:
        """Returns cached workspace context if fresh, else re-scans."""
        with self._ws_cache_lock:
            if self._ws_cache is not None:
                ctx, ts = self._ws_cache
                if time.monotonic() - ts < settings.scan_ttl:
                    logger.debug("Returning cached workspace context (within scan_ttl).")
                    return ctx
        try:
            ctx = self.workspace_scanner.scan()
            with self._ws_cache_lock:
                self._ws_cache = (ctx, time.monotonic())
            return ctx
        except Exception as e:
            logger.error(f"Workspace scan failed: {e}")
            return None

    def analyze_content(self, content: str) -> AnalysisResult:
        """Processes raw trace text. Returns cached result if same content was recently analyzed."""
        logger.info("Initializing traceback analysis sequence.")

        # ── Cache lookup ──────────────────────────────────────────────────────
        content_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
        cached = self._cache.get(content_hash)
        if cached is not None:
            logger.info("Returning cached analysis result (content hash matched).")
            return cached

        # ── 1. Parse ──────────────────────────────────────────────────────────
        parser = LogParser()
        if not parser.can_parse(content):
            logger.warning("Input content structure not recognized; attempting parsing with fallback strategies.")

        error = parser.parse(content)
        logger.info(f"Traceback parsed successfully. Language: {error.language}. Type: {error.error_type}")

        # ── 2. Local source context ───────────────────────────────────────────
        logger.info("Starting local file code snippet indexing...")
        self.analyzer.analyze(error)

        # ── 3. Call graph ─────────────────────────────────────────────────────
        logger.info("Compiling propagation dependency graph...")
        graph = Graph()
        populate_error_graph(graph, error)

        # ── 4. Workspace intelligence (cached) ────────────────────────────────
        logger.info("Running workspace intelligence scan...")
        workspace_context = self._get_workspace_context()

        # ── 5. Git frame annotation ───────────────────────────────────────────
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

        # ── 6. AST + Symbol Graph ─────────────────────────────────────────────
        logger.info("Running AST and symbol graph builder...")
        symbol_graph_data = None
        try:
            builder = SymbolGraphBuilder(
                self.project_root,
                max_files_to_parse=settings.max_scan_files,
                max_nodes=settings.max_graph_nodes
            )
            builder.build()
            analyzer_sg = SymbolGraphAnalyzer(self.project_root, builder)
            all_smells = analyzer_sg.analyze()

            # Collect traceback files
            traceback_files: set = set()
            def collect_files(err):
                for frame in err.frames:
                    if frame.file_path:
                        try:
                            rel = str(Path(frame.file_path).resolve().relative_to(self.project_root).as_posix())
                            traceback_files.add(rel)
                        except Exception:
                            traceback_files.add(frame.file_path)
                for chained in err.chained_errors:
                    collect_files(chained)
            collect_files(error)

            filtered_smells = [
                smell for smell in all_smells
                if smell.file_path in traceback_files or any(f in smell.file_path for f in traceback_files)
            ]
            serialized = builder.to_serialized_data()
            serialized.smells = filtered_smells
            symbol_graph_data = serialized
        except Exception as e:
            logger.error(f"Symbol graph analysis failed: {e}")

        # ── 7. Root Cause Analysis ────────────────────────────────────────────
        logger.info("Running Root Cause Analysis static analyzer...")
        rca_result = None
        try:
            rca_analyzer = RootCauseAnalyzer(self.project_root)
            rca_result = rca_analyzer.analyze(error, workspace_context, symbol_graph_data)
        except Exception as e:
            logger.error(f"Root Cause Analysis failed: {e}")

        # ── 8. LLM Reasoning ─────────────────────────────────────────────────
        logger.info(f"Querying reasoning intelligence (provider: {self.llm_provider})...")
        recommendation = self.llm_client.analyze_error(
            error,
            workspace_context=workspace_context,
            symbol_graph_data=symbol_graph_data,
            rca_result=rca_result
        )

        # ── 9. Remediation ────────────────────────────────────────────────────
        logger.info("Running Remediation static and AI-augmented engine...")
        remediation_result = None
        try:
            rem_engine = RemediationEngine(self.project_root)
            remediation_result = rem_engine.generate_suggestions(
                error,
                workspace_context=workspace_context,
                symbol_graph_data=symbol_graph_data,
                rca_result=rca_result,
                recommendation=recommendation
            )
        except Exception as e:
            logger.error(f"Remediation analysis failed: {e}")

        logger.info("Traceback analysis sequence completed successfully.")
        result = AnalysisResult(
            error=error,
            recommendation=recommendation,
            graph=graph.to_dict(),
            workspace_context=workspace_context,
            symbol_graph_data=symbol_graph_data,
            rca_result=rca_result,
            remediation_result=remediation_result
        )

        # Store in cache
        self._cache.put(content_hash, result)
        return result

    def analyze_file(self, file_path: Path) -> AnalysisResult:
        """Reads trace file and performs analysis."""
        resolved_path = Path(file_path).resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"Traceback source file not found: {resolved_path}")

        logger.info(f"Reading target traceback file: {resolved_path}")
        with open(resolved_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        return self.analyze_content(content)

    @property
    def cache_info(self) -> Dict[str, Any]:
        """Returns diagnostic info about the result cache."""
        return {
            "cache_size": self._cache.size(),
            "max_cache_size": settings.cache_size,
            "cache_ttl_seconds": settings.cache_ttl,
        }
