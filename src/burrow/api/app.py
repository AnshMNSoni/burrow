from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from burrow.core.engine import BurrowEngine, AnalysisResult
from burrow.utils.logging import logger, setup_logging
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


@app.post("/api/v1/analyze", response_model=AnalysisResult)
def analyze(request: AnalyzeRequest):
    """Processes traceback payload and returns normalized engine analysis."""
    try:
        engine = BurrowEngine(project_root=request.project_root)
        result = engine.analyze_content(request.content)
        return result
    except Exception as e:
        logger.exception("Error processing traceback via API")
        raise HTTPException(status_code=500, detail=str(e))
