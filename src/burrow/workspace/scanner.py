import os
import re
import subprocess
import json
import tomllib  # Python 3.11 standard library
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# Module-level Tree-sitter language objects (immutable, safe to share)
_PY_LANGUAGE = Language(tspython.language())
_JS_LANGUAGE = Language(tsjs.language())
_TS_LANGUAGE = Language(tstypescript.language_typescript())
_TSX_LANGUAGE = Language(tstypescript.language_tsx())

# Git subprocess timeout (seconds) — prevents hangs on slow remotes / large histories
_GIT_TIMEOUT = 5


class WorkspaceScanner:
    """Recursively scans project repository layout, tracks Git context, and parses code files using Tree-sitter."""

    def __init__(self, project_root: Path, max_files_to_parse: int = 300):
        self.project_root = Path(project_root).resolve()
        self.max_files_to_parse = max_files_to_parse

        # Instance-level parsers — created once, reused for all files
        self._py_parser = Parser(_PY_LANGUAGE)
        self._js_parser = Parser(_JS_LANGUAGE)
        self._ts_parser = Parser(_TS_LANGUAGE)
        self._tsx_parser = Parser(_TSX_LANGUAGE)

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

    def _parse_file_imports(self, path: Path) -> List[ImportRelation]:
        """Parses a single file and returns its import relations. Designed to run in a thread."""
        rel_path_str = str(path.relative_to(self.project_root).as_posix())
        ext = path.suffix.lower()
        relations: List[ImportRelation] = []
        try:
            with open(path, "rb") as f:
                code_bytes = f.read()
            if ext == ".py":
                tree = self._py_parser.parse(code_bytes)
                self._walk_python_imports(tree.root_node, code_bytes, rel_path_str, relations)
            elif ext in (".js", ".jsx"):
                tree = self._js_parser.parse(code_bytes)
                self._walk_js_imports(tree.root_node, code_bytes, rel_path_str, relations)
            elif ext == ".ts":
                tree = self._ts_parser.parse(code_bytes)
                self._walk_js_imports(tree.root_node, code_bytes, rel_path_str, relations)
            elif ext == ".tsx":
                tree = self._tsx_parser.parse(code_bytes)
                self._walk_js_imports(tree.root_node, code_bytes, rel_path_str, relations)
        except Exception as e:
            logger.debug(f"Failed to parse imports for file {path}: {e}")
        return relations

    def _map_imports(self, structure: WorkspaceStructure) -> List[ImportRelation]:
        """Collects import relations across all source files using a thread pool."""
        exclude_dirs = {
            ".git", "node_modules", "venv", ".venv", "dist", "build",
            ".next", ".pytest_cache", "__pycache__"
        }

        # Collect eligible files up to the cap
        source_files: List[Path] = []
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for file in files:
                if len(source_files) >= self.max_files_to_parse:
                    break
                p = Path(root) / file
                if p.suffix.lower() in (".py", ".js", ".jsx", ".ts", ".tsx"):
                    source_files.append(p)
            if len(source_files) >= self.max_files_to_parse:
                break

        import_relations: List[ImportRelation] = []
        max_workers = min(8, os.cpu_count() or 4)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self._parse_file_imports, p): p for p in source_files}
            for future in as_completed(futures):
                try:
                    import_relations.extend(future.result())
                except Exception as e:
                    logger.debug(f"Import parse worker error: {e}")

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

    def _run_git(self, args: List[str]) -> Optional[str]:
        """Runs a git command with a bounded timeout. Returns stdout or None on failure."""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=True,
                timeout=_GIT_TIMEOUT
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            logger.debug(f"git {' '.join(args)} timed out after {_GIT_TIMEOUT}s — skipping.")
            return None
        except subprocess.CalledProcessError as e:
            logger.debug(f"git {' '.join(args)} failed: {e.stderr.strip()}")
            return None
        except Exception as e:
            logger.debug(f"git {' '.join(args)} error: {e}")
            return None

    def _extract_git_context(self) -> Optional[GitContext]:
        """Extracts branch, file status, recent history, and current diff from git."""
        if not (self.project_root / ".git").exists():
            return None

        try:
            branch_out = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
            if branch_out is None:
                return None
            branch = branch_out.strip()

            status_out = self._run_git(["status", "--porcelain", "-u"])
            recent_changes: List[GitFileStatus] = []
            seen_files: Set[str] = set()

            for line in (status_out or "").splitlines():
                if not line or len(line) < 4:
                    continue
                state = line[:2].strip()
                filepath = line[3:].strip()
                if " -> " in filepath:
                    filepath = filepath.split(" -> ")[-1].strip()
                status_desc = (
                    "modified" if "M" in state else
                    "untracked" if "?" in state else
                    "added" if "A" in state else
                    "deleted" if "D" in state else "modified"
                )
                recent_changes.append(GitFileStatus(file_path=filepath, status=status_desc))
                seen_files.add(filepath)

            # Cap history at 20 commits to keep git log fast
            log_out = self._run_git(["log", "--max-count=20", "--name-only", "--pretty=format:"])
            for lf in (log_out or "").splitlines():
                lf = lf.strip()
                if lf and lf not in seen_files and (self.project_root / lf).exists():
                    recent_changes.append(GitFileStatus(file_path=lf, status="committed"))
                    seen_files.add(lf)

            diff_out = self._run_git(["diff"])
            diff = (diff_out or "")[:2000]
            if len(diff_out or "") > 2000:
                diff += "\n... (diff truncated for size) ..."

            return GitContext(
                active_branch=branch,
                recent_changes=recent_changes,
                current_diff=diff if diff.strip() else None
            )

        except Exception as e:
            logger.debug(f"Failed to read Git history/context: {e}")
            return None
