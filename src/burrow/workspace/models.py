from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class DependencyInfo(BaseModel):
    name: str
    version: Optional[str] = None
    scope: str = "production"  # e.g., production, dev

class WorkspaceStructure(BaseModel):
    detected_frameworks: List[str] = Field(default_factory=list)
    package_managers: List[str] = Field(default_factory=list)  # e.g., npm, pip
    config_files: List[str] = Field(default_factory=list)      # relative paths
    entrypoints: List[str] = Field(default_factory=list)
    env_files: List[str] = Field(default_factory=list)

class GitFileStatus(BaseModel):
    file_path: str
    status: str  # e.g., modified, untracked, added, deleted
    last_modified: Optional[str] = None

class GitContext(BaseModel):
    active_branch: str
    recent_changes: List[GitFileStatus] = Field(default_factory=list)
    current_diff: Optional[str] = None

class ImportRelation(BaseModel):
    source_file: str
    target_module: str
    is_relative: bool = False

class WorkspaceContext(BaseModel):
    structure: WorkspaceStructure
    dependencies: Dict[str, List[DependencyInfo]] = Field(default_factory=dict)
    import_map: List[ImportRelation] = Field(default_factory=list)
    git: Optional[GitContext] = None
