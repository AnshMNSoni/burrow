from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Any, Dict
from burrow.core.engine import BurrowEngine, AnalysisResult
from burrow.utils.logging import logger
from burrow.config import settings

app = FastAPI(
    title="Burrow API",
    description="Local-first AI debugging intelligence backend",
    version="0.1.0"
)


class AnalyzeRequest(BaseModel):
    content: str
    project_root: Optional[str] = None


@app.get("/health")
def health():
    """Service health check endpoint."""
    return {"status": "ok", "service": "burrow"}


@app.get("/api/v1/health/detailed")
def health_detailed():
    """Detailed health endpoint — reports engine cache state and configuration."""
    engine = BurrowEngine.get_shared()
    return {
        "status": "ok",
        "service": "burrow",
        "llm_provider": settings.llm_provider,
        "cache": engine.cache_info,
        "scan_ttl_seconds": settings.scan_ttl,
        "max_scan_files": settings.max_scan_files,
        "max_graph_nodes": settings.max_graph_nodes,
        "max_request_size_chars": settings.max_request_size,
    }


@app.post("/api/v1/analyze", response_model=AnalysisResult)
def analyze(request: AnalyzeRequest):
    """Processes traceback payload and returns normalized engine analysis."""
    # Payload size guard
    if len(request.content) > settings.max_request_size:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Request payload too large: {len(request.content)} characters. "
                f"Maximum allowed is {settings.max_request_size}. "
                "Trim the log content and retry."
            )
        )

    try:
        # Use shared singleton engine — avoids re-initializing scanner/parsers on every request
        engine = BurrowEngine.get_shared(
            project_root=request.project_root,
        )
        result = engine.analyze_content(request.content)

        # Signal whether result was served from cache
        response = JSONResponse(
            content=result.model_dump(mode="json"),
            headers={"X-Burrow-Cached": "false"},  # engine logs when cached; header set below
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error processing traceback via API")
        raise HTTPException(status_code=500, detail=str(e))
