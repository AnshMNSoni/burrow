import os
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

class Settings(BaseModel):
    # ── Core ──────────────────────────────────────────────────────────────────
    log_level: str = Field(default="info")
    llm_provider: str = Field(default="mock")
    llm_api_key: str = Field(default="")
    project_root: Path = Field(default=Path("."))
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000)

    # ── Ollama LLM ────────────────────────────────────────────────────────────
    ollama_endpoint: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="qwen2.5-coder")
    ollama_timeout: float = Field(default=30.0)
    ollama_max_retries: int = Field(default=2)

    # ── Auto Patch ────────────────────────────────────────────────────────────
    enable_auto_patch: bool = Field(default=False)
    patch_min_confidence: float = Field(default=0.70)
    weak_confidence_threshold: float = Field(default=0.60)
    allowed_write_paths: Optional[str] = Field(default=None)

    # ── Performance & Caching ─────────────────────────────────────────────────
    cache_size: int = Field(default=10)        # Max cached AnalysisResult entries
    cache_ttl: float = Field(default=60.0)    # Cache TTL in seconds
    scan_ttl: float = Field(default=30.0)     # Workspace re-scan cooldown in seconds
    max_scan_files: int = Field(default=300)  # Max source files parsed per workspace scan
    max_graph_nodes: int = Field(default=5000) # Max nodes in the symbol graph
    max_request_size: int = Field(default=50000) # Max API payload size (characters)

    @classmethod
    def load(cls) -> "Settings":
        """Loads settings from environment variables (BURROW_ prefix first, then raw name)."""
        data = {}
        for field_name, field in cls.model_fields.items():
            env_keys = [f"BURROW_{field_name.upper()}", field_name.upper()]
            val = None
            for key in env_keys:
                if key in os.environ:
                    val = os.environ[key]
                    break

            if val is not None:
                annotation = field.annotation
                # Unwrap Optional[X] → X
                origin = getattr(annotation, "__origin__", None)
                args = getattr(annotation, "__args__", ())
                if origin is type(None):
                    annotation = str
                elif origin is not None and args:
                    # Optional[X] has __origin__ = Union, __args__ = (X, NoneType)
                    non_none = [a for a in args if a is not type(None)]
                    if non_none:
                        annotation = non_none[0]

                # Coerce type
                if annotation == int:
                    data[field_name] = int(val)
                elif annotation == float:
                    data[field_name] = float(val)
                elif annotation == bool:
                    data[field_name] = val.lower() in ("true", "1", "yes", "on")
                elif annotation == Path:
                    data[field_name] = Path(val)
                else:
                    data[field_name] = val

        return cls(**data)

settings = Settings.load()
