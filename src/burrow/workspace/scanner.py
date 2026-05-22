import os
import re
import subprocess
import json
import tomllib  # Python 3.11 standard library
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set

from tree_sitter import Language, Parser
import tree_sitter_python as tspython
import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tstypescript

from burrow.workspace.models import (
    WorkspaceContext,
    WorkspaceStructure,
    DependencyInfo,
    GitFileStatus,
    GitContext,
    ImportRelation
)
from burrow.utils.logging import logger

# Initialize Tree-sitter languages
PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjs.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())

class WorkspaceScanner:
    """Recursively scans project repository layout, tracks Git context, and parses code files using Tree-sitter."""

    def __init__(self, project_root: Path, max_files_to_parse: int = 300):
        self.project_root = Path(project_root).resolve()
        self.max_files_to_parse = max_files_to_parse

    def scan(self) -> WorkspaceContext:
        """Runs the complete static analysis scan of the repository."""
        logger.debug(f"Starting workspace scan of {self.project_root}")
        
        # 1. Structure and framework detection
        structure, dependencies = self._scan_structure_and_dependencies()
        
        # 2. Tree-sitter AST Import mapping
        import_map = self._map_imports(structure)
        
        # 3. Add any frameworks inferred from imports
        inferred_frameworks = self._infer_frameworks_from_imports(import_map)
        for fw in inferred_frameworks:
            if fw not in structure.detected_frameworks:
                structure.detected_frameworks.append(fw)

        # 4. Extract Git Context
        git_context = self._extract_git_context()

        return WorkspaceContext(
            structure=structure,
            dependencies=dependencies,
            import_map=import_map,
            git=git_context
        )

    def _scan_structure_and_dependencies(self) -> Tuple[WorkspaceStructure, Dict[str, List[DependencyInfo]]]:
        structure = WorkspaceStructure()
        dependencies: Dict[str, List[DependencyInfo]] = {}

        exclude_dirs = {
            ".git", "node_modules", "venv", ".venv", "dist", "build", 
            ".next", ".pytest_cache", "__pycache__", "out", "target"
        }

        config_signatures = {
            "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
            "requirements.txt", "pyproject.toml", "Pipfile", "go.mod", 
            "Cargo.toml", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
            "tsconfig.json", "next.config.js", "next.config.mjs", "webpack.config.js"
        }

        # Recursively walk to find files
        for root, dirs, files in os.walk(self.project_root):
            # Prune directory search
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            
            for file in files:
                rel_path = Path(root).relative_to(self.project_root) / file
                rel_path_str = str(rel_path.as_posix())
                
                # Check for env files
                if file.startswith(".env"):
                    structure.env_files.append(rel_path_str)
                    continue

                if file in config_signatures:
                    structure.config_files.append(rel_path_str)

                    # Framework & package manager detection
                    if file == "package.json":
                        structure.package_managers.append("npm/yarn/pnpm")
                        self._parse_package_json(self.project_root / rel_path, structure, dependencies)
                    elif file == "requirements.txt":
                        if "pip" not in structure.package_managers:
                            structure.package_managers.append("pip")
                        self._parse_requirements_txt(self.project_root / rel_path, structure, dependencies)
                    elif file == "pyproject.toml":
                        if "pip/poetry" not in structure.package_managers:
                            structure.package_managers.append("pip/poetry")
                        self._parse_pyproject_toml(self.project_root / rel_path, structure, dependencies)
                    elif file == "go.mod":
                        structure.package_managers.append("go-modules")
                        if "go" not in structure.detected_frameworks:
                            structure.detected_frameworks.append("go")
                    elif file == "Cargo.toml":
                        structure.package_managers.append("cargo")
                        if "rust" not in structure.detected_frameworks:
                            structure.detected_frameworks.append("rust")
                    elif file in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml"):
                        if "docker" not in structure.detected_frameworks:
                            structure.detected_frameworks.append("docker")
                    elif file in ("next.config.js", "next.config.mjs"):
                        if "next.js" not in structure.detected_frameworks:
                            structure.detected_frameworks.append("next.js")

                # Heuristic for entrypoints
                if file in ("main.py", "app.py", "index.js", "index.ts", "server.js", "server.ts", "wsgi.py", "asgi.py"):
                    structure.entrypoints.append(rel_path_str)

        return structure, dependencies

    def _parse_package_json(self, path: Path, structure: WorkspaceStructure, dependencies: Dict[str, List[DependencyInfo]]):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
            
            deps_list = []
            
            # Extract standard dependencies
            prod_deps = data.get("dependencies", {})
            for name, ver in prod_deps.items():
                deps_list.append(DependencyInfo(name=name, version=str(ver), scope="production"))
                # Framework heuristics
                if name == "react" and "react" not in structure.detected_frameworks:
                    structure.detected_frameworks.append("react")
                if name == "next" and "next.js" not in structure.detected_frameworks:
                    structure.detected_frameworks.append("next.js")
                if name == "express" and "express" not in structure.detected_frameworks:
                    structure.detected_frameworks.append("express")
            
            # Extract dev dependencies
            dev_deps = data.get("devDependencies", {})
            for name, ver in dev_deps.items():
                deps_list.append(DependencyInfo(name=name, version=str(ver), scope="dev"))

            dependencies[str(path.relative_to(self.project_root).as_posix())] = deps_list
        except Exception as e:
            logger.debug(f"Failed to parse package.json {path}: {e}")

    def _parse_requirements_txt(self, path: Path, structure: WorkspaceStructure, dependencies: Dict[str, List[DependencyInfo]]):
        try:
            deps_list = []
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-r"):
                        continue
                    
                    # Split version constraints: package==1.0 or package>=1.0
                    match = re.split(r'==|>=|<=|>|<|~=', line, 1)
                    name = match[0].strip()
                    version = match[1].strip() if len(match) > 1 else None
                    deps_list.append(DependencyInfo(name=name, version=version, scope="production"))
                    
                    if name.lower() in ("fastapi", "django", "flask") and name.lower() not in structure.detected_frameworks:
                        structure.detected_frameworks.append(name.lower())
                    
            dependencies[str(path.relative_to(self.project_root).as_posix())] = deps_list
        except Exception as e:
            logger.debug(f"Failed to parse requirements.txt {path}: {e}")

    def _parse_pyproject_toml(self, path: Path, structure: WorkspaceStructure, dependencies: Dict[str, List[DependencyInfo]]):
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            
            deps_list = []
            
            # Standard PEP 621 dependencies
            project = data.get("project", {})
            for dep in project.get("dependencies", []):
                # E.g. "pydantic>=2.0.0"
                match = re.split(r'==|>=|<=|>|<|~=', dep, 1)
                name = match[0].strip()
                version = match[1].strip() if len(match) > 1 else None
                deps_list.append(DependencyInfo(name=name, version=version, scope="production"))
                
                if name.lower() in ("fastapi", "django", "flask") and name.lower() not in structure.detected_frameworks:
                    structure.detected_frameworks.append(name.lower())

            # Poetry dependencies
            tool_poetry = data.get("tool", {}).get("poetry", {})
            for name, val in tool_poetry.get("dependencies", {}).items():
                if name.lower() == "python":
                    continue
                ver = val if isinstance(val, str) else val.get("version") if isinstance(val, dict) else None
                deps_list.append(DependencyInfo(name=name, version=ver, scope="production"))
                if name.lower() in ("fastapi", "django", "flask") and name.lower() not in structure.detected_frameworks:
                    structure.detected_frameworks.append(name.lower())

            dependencies[str(path.relative_to(self.project_root).as_posix())] = deps_list
        except Exception as e:
            logger.debug(f"Failed to parse pyproject.toml {path}: {e}")

    def _map_imports(self, structure: WorkspaceStructure) -> List[ImportRelation]:
        import_relations: List[ImportRelation] = []
        parsed_count = 0

        exclude_dirs = {
            ".git", "node_modules", "venv", ".venv", "dist", "build", 
            ".next", ".pytest_cache", "__pycache__"
        }

        # Initialize parsers
        py_parser = Parser(PY_LANGUAGE)
        js_parser = Parser(JS_LANGUAGE)
        ts_parser = Parser(TS_LANGUAGE)
        tsx_parser = Parser(TSX_LANGUAGE)

        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            
            for file in files:
                if parsed_count >= self.max_files_to_parse:
                    break
                
                path = Path(root) / file
                ext = path.suffix.lower()
                
                if ext not in (".py", ".js", ".jsx", ".ts", ".tsx"):
                    continue

                rel_path = path.relative_to(self.project_root)
                rel_path_str = str(rel_path.as_posix())
                
                try:
                    with open(path, "rb") as f:
                        code_bytes = f.read()

                    # Select parser
                    if ext == ".py":
                        tree = py_parser.parse(code_bytes)
                        self._walk_python_imports(tree.root_node, code_bytes, rel_path_str, import_relations)
                    elif ext in (".js", ".jsx"):
                        tree = js_parser.parse(code_bytes)
                        self._walk_js_imports(tree.root_node, code_bytes, rel_path_str, import_relations)
                    elif ext == ".ts":
                        tree = ts_parser.parse(code_bytes)
                        self._walk_js_imports(tree.root_node, code_bytes, rel_path_str, import_relations)
                    elif ext == ".tsx":
                        tree = tsx_parser.parse(code_bytes)
                        self._walk_js_imports(tree.root_node, code_bytes, rel_path_str, import_relations)

                    parsed_count += 1
                except Exception as e:
                    logger.debug(f"Failed to parse imports for file {path}: {e}")

        return import_relations

    def _walk_python_imports(self, node, code_bytes: bytes, source_file: str, imports: List[ImportRelation]):
        if node.type == 'import_statement':
            for child in node.children:
                if child.type == 'dotted_name':
                    mod = code_bytes[child.start_byte:child.end_byte].decode('utf-8', errors='ignore')
                    imports.append(ImportRelation(source_file=source_file, target_module=mod, is_relative=False))
                elif child.type in ('aliased_import_name', 'aliased_import'):
                    name_node = None
                    for sub in child.children:
                        if sub.type == 'dotted_name':
                            name_node = sub
                            break
                    if name_node:
                        mod = code_bytes[name_node.start_byte:name_node.end_byte].decode('utf-8', errors='ignore')
                        imports.append(ImportRelation(source_file=source_file, target_module=mod, is_relative=False))
        elif node.type == 'import_from_statement':
            is_relative = False
            dotted_name_node = None
            for child in node.children:
                if child.type == 'import':
                    break
                if child.type == '.':
                    is_relative = True
                elif child.type == 'dotted_name':
                    dotted_name_node = child
                elif child.type == 'relative_import':
                    is_relative = True
                    for sub in child.children:
                        if sub.type == 'dotted_name':
                            dotted_name_node = sub
            
            if dotted_name_node:
                mod = code_bytes[dotted_name_node.start_byte:dotted_name_node.end_byte].decode('utf-8', errors='ignore')
                imports.append(ImportRelation(source_file=source_file, target_module=mod, is_relative=is_relative))
            elif is_relative:
                # E.g. "from . import foo"
                imports.append(ImportRelation(source_file=source_file, target_module="", is_relative=True))

        for child in node.children:
            self._walk_python_imports(child, code_bytes, source_file, imports)

    def _walk_js_imports(self, node, code_bytes: bytes, source_file: str, imports: List[ImportRelation]):
        if node.type == 'import_statement':
            source_node = node.child_by_field_name('source')
            if source_node:
                mod = code_bytes[source_node.start_byte:source_node.end_byte].decode('utf-8', errors='ignore')
                mod = mod.strip("'\"")
                is_rel = mod.startswith(".")
                imports.append(ImportRelation(source_file=source_file, target_module=mod, is_relative=is_rel))
        elif node.type == 'call_expression':
            func_node = node.child_by_field_name('function')
            if func_node and func_node.type == 'identifier':
                func_name = code_bytes[func_node.start_byte:func_node.end_byte].decode('utf-8', errors='ignore')
                if func_name == 'require':
                    arg_list = node.child_by_field_name('arguments')
                    if arg_list and arg_list.children:
                        for arg in arg_list.children:
                            if arg.type in ('string', 'string_fragment', 'string_literal'):
                                mod = code_bytes[arg.start_byte:arg.end_byte].decode('utf-8', errors='ignore')
                                mod = mod.strip("'\"")
                                is_rel = mod.startswith(".")
                                imports.append(ImportRelation(source_file=source_file, target_module=mod, is_relative=is_rel))
                                break

        for child in node.children:
            self._walk_js_imports(child, code_bytes, source_file, imports)

    def _infer_frameworks_from_imports(self, import_map: List[ImportRelation]) -> Set[str]:
        inferred = set()
        for rel in import_map:
            target = rel.target_module.lower()
            if target == "fastapi":
                inferred.add("fastapi")
            elif target == "flask":
                inferred.add("flask")
            elif target == "django":
                inferred.add("django")
            elif target == "react":
                inferred.add("react")
            elif target == "next" or target.startswith("next/"):
                inferred.add("next.js")
            elif target == "express":
                inferred.add("express")
        return inferred

    def _extract_git_context(self) -> Optional[GitContext]:
        # Check if project root is a git repository
        if not (self.project_root / ".git").exists():
            return None

        try:
            # Check active branch
            branch_run = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.project_root, capture_output=True, text=True, check=True
            )
            branch = branch_run.stdout.strip()
            
            # Check git status (unstaged changes)
            status_run = subprocess.run(
                ["git", "status", "--porcelain", "-u"],
                cwd=self.project_root, capture_output=True, text=True, check=True
            )
            
            recent_changes: List[GitFileStatus] = []
            seen_files = set()
            
            status_lines = status_run.stdout.splitlines()
            for line in status_lines:
                if not line or len(line) < 4:
                    continue
                state = line[:2].strip()
                filepath = line[3:].strip()
                
                # Handle renames e.g. "R  old.py -> new.py"
                if " -> " in filepath:
                    filepath = filepath.split(" -> ")[-1].strip()
                
                status_desc = "modified"
                if "M" in state:
                    status_desc = "modified"
                elif "?" in state:
                    status_desc = "untracked"
                elif "A" in state:
                    status_desc = "added"
                elif "D" in state:
                    status_desc = "deleted"
                
                recent_changes.append(GitFileStatus(
                    file_path=filepath,
                    status=status_desc
                ))
                seen_files.add(filepath)

            # Check last 10 changed files in Git history to provide historical recently modified files
            log_run = subprocess.run(
                ["git", "log", "-n", "10", "--name-only", "--pretty=format:"],
                cwd=self.project_root, capture_output=True, text=True, check=True
            )
            log_files = [f.strip() for f in log_run.stdout.splitlines() if f.strip()]
            for lf in log_files:
                if lf not in seen_files:
                    # Check if file exists to avoid deleted files in log
                    if (self.project_root / lf).exists():
                        recent_changes.append(GitFileStatus(
                            file_path=lf,
                            status="committed"
                        ))
                        seen_files.add(lf)

            # Get current unsaved git diff (cap at 2000 chars)
            diff_run = subprocess.run(
                ["git", "diff"],
                cwd=self.project_root, capture_output=True, text=True, check=True
            )
            diff = diff_run.stdout
            if len(diff) > 2000:
                diff = diff[:2000] + "\n... (diff truncated for size) ..."
                
            return GitContext(
                active_branch=branch,
                recent_changes=recent_changes,
                current_diff=diff if diff.strip() else None
            )

        except Exception as e:
            logger.debug(f"Failed to read Git history/context: {e}")
            return None
