import os
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

class Settings(BaseModel):
    log_level: str = Field(default="info")
    llm_provider: str = Field(default="mock")
    llm_api_key: str = Field(default="")
    project_root: Path = Field(default=Path("."))
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000)
    ollama_endpoint: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="qwen2.5-coder")

    @classmethod
    def load(cls) -> "Settings":
        # Load from environment variables mapping to fields (try BURROW_ prefix first, then raw name)
        data = {}
        for field_name, field in cls.model_fields.items():
            env_keys = [f"BURROW_{field_name.upper()}", field_name.upper()]
            val = None
            for key in env_keys:
                if key in os.environ:
                    val = os.environ[key]
                    break
            
            if val is not None:
                if field.annotation == int:
                    data[field_name] = int(val)
                elif field.annotation == Path:
                    data[field_name] = Path(val)
                else:
                    data[field_name] = val
        return cls(**data)

settings = Settings.load()
