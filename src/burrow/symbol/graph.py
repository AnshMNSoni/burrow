import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple
import networkx as nx

from burrow.symbol.models import SymbolDetail, SymbolType, CallSiteDetail, SymbolGraphData
from burrow.symbol.extractor import extract_symbols, ExtractedFileInfo, ImportBinding
from burrow.utils.logging import logger

def resolve_module_to_file(importing_file: str, module_path: str, is_relative: bool, scanned_files: Set[str]) -> Optional[str]:
    """Resolves an import module path to a relative file path present in scanned_files."""
    if not module_path:
        return None
        
    importing_path = Path(importing_file)
    importing_dir = importing_path.parent
    
    suffixes = [".py", ".ts", ".tsx", ".js", ".jsx"]
    check_paths = []
    
    if is_relative:
        clean_path = module_path
        dots = 0
        while clean_path.startswith("."):
            dots += 1
            clean_path = clean_path[1:]
            
        target_dir = importing_dir
        for _ in range(dots - 1):
            if target_dir.parent != target_dir:  # Avoid infinite root loop
                target_dir = target_dir.parent
            
        path_parts = clean_path.replace(".", "/").strip("/")
        if path_parts:
            rel_target = target_dir / path_parts
        else:
            rel_target = target_dir
        check_paths.append(rel_target)
    else:
        path_parts = module_path.replace(".", "/")
        rel_target = Path(path_parts)
        check_paths.append(rel_target)
        check_paths.append(Path("src") / rel_target)
        
    for cp in check_paths:
        cp_str = str(cp.as_posix()).strip("/")
        if cp_str.startswith("./"):
            cp_str = cp_str[2:]
            
        # 1. Direct file check with suffix
        for suffix in suffixes:
            path_with_suffix = cp_str + suffix
            if path_with_suffix in scanned_files:
                return path_with_suffix
                
        # 2. Package index files check
        for suffix in suffixes:
            if suffix == ".py":
                idx_path = cp_str + "/__init__.py"
            else:
                idx_path = cp_str + f"/index{suffix}"
            if idx_path in scanned_files:
                return idx_path
                
        # 3. Exact match check
        if cp_str in scanned_files:
            return cp_str
            
    return None


class SymbolGraphBuilder:
    def __init__(self, project_root: Path, max_files_to_parse: int = 300, max_nodes: int = 5000):
        self.project_root = Path(project_root).resolve()
        self.max_files_to_parse = max_files_to_parse
        self.max_nodes = max_nodes
        self.g = nx.DiGraph()
        
        # Maps file path -> ExtractedFileInfo
        self.file_info_map: Dict[str, ExtractedFileInfo] = {}
        
        # Maps class name globally/locally -> Class SymbolDetail
        # We also keep a lookup of (file_path, class_name) -> Class SymbolDetail
        self.class_lookup: Dict[Tuple[str, str], SymbolDetail] = {}
        
        # Maps method name -> list of (file_path, class_name, SymbolDetail)
        self.method_lookup: Dict[str, List[Tuple[str, str, SymbolDetail]]] = {}

    def build(self) -> nx.DiGraph:
        """Scans the workspace, extracts symbols, builds nodes & edges, and resolves call sites."""
        logger.info(f"Starting symbol graph build for {self.project_root}")
        
        # 1. Find and scan all source files
        exclude_dirs = {
            ".git", "node_modules", "venv", ".venv", "dist", "build", 
            ".next", ".pytest_cache", "__pycache__", "out", "target"
        }
        
        source_files: List[Path] = []
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for file in files:
                ext = Path(file).suffix.lower()
                if ext in (".py", ".js", ".jsx", ".ts", ".tsx"):
                    source_files.append(Path(root) / file)
                    if len(source_files) >= self.max_files_to_parse:
                        break
            if len(source_files) >= self.max_files_to_parse:
                break
                
        # Extract AST info for all files
        for f in source_files:
            if self.g.number_of_nodes() >= self.max_nodes:
                logger.warning(
                    f"Symbol graph node cap ({self.max_nodes}) reached after {len(self.file_info_map)} files. "
                    "Remaining files skipped. Raise BURROW_MAX_GRAPH_NODES to index more."
                )
                break
            info = extract_symbols(f, self.project_root)
            if info:
                self.file_info_map[info.file_path] = info
                
        scanned_files = set(self.file_info_map.keys())
        
        # 2. Add File and Symbol Nodes and "defines" edges
        for file_path, info in self.file_info_map.items():
            file_node_id = f"file:{file_path}"
            self.g.add_node(file_node_id, type="file", path=file_path)
            
            for sym in info.symbols:
                sym_node_id = f"symbol:{file_path}:{sym.name}"
                self.g.add_node(
                    sym_node_id,
                    type="symbol",
                    symbol_type=sym.type.value,
                    name=sym.name,
                    file_path=file_path,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    docstring=sym.docstring,
                    signature=sym.signature
                )
                
                # defines edge
                if sym.type == SymbolType.METHOD:
                    # Method parent should be its class in the same file
                    parent_class = sym.name.split('.')[0] if '.' in sym.name else None
                    if not parent_class:
                        # Fallback: check if we are currently nesting
                        parent_class = sym.name.split('_')[0]  # heuristic
                    
                    # Store method globally
                    method_short_name = sym.name.split('.')[-1]
                    if method_short_name not in self.method_lookup:
                        self.method_lookup[method_short_name] = []
                    self.method_lookup[method_short_name].append((file_path, parent_class or "", sym))
                    
                    class_node_id = f"symbol:{file_path}:{parent_class}"
                    if parent_class and self.g.has_node(class_node_id):
                        self.g.add_edge(class_node_id, sym_node_id, relation="defines")
                    else:
                        self.g.add_edge(file_node_id, sym_node_id, relation="defines")
                else:
                    self.g.add_edge(file_node_id, sym_node_id, relation="defines")
                    
                if sym.type == SymbolType.CLASS:
                    self.class_lookup[(file_path, sym.name)] = sym

        # 3. Process imports and resolve calls
        for file_path, info in self.file_info_map.items():
            file_node_id = f"file:{file_path}"
            
            # Simple local type inference mapping
            # Variable -> Class Name
            local_type_inference: Dict[str, str] = {}
            # Let's inspect class assignments (e.g. obj = MyClass())
            # In Python/JS, if we see a call: callee is MyClass
            # And it is in an assignment to a variable 'obj'
            # We will handle it by walking references or assignments
            # Let's check calls inside this file
            for call in info.calls:
                # If caller is None (top level of module) or inside a function
                # If the call is a Class constructor call (callee is a known class name)
                # and line_number corresponds to a local variable assignment
                # Since we don't have complete AST assignment tracking here, we can heuristically match:
                # if there is a reference at line_number on the left side, or let's keep it simple:
                # if callee name is a known class (either imported or local), we map it.
                pass

            # Add inter-file import edges
            for imp in info.imports:
                target_file = resolve_module_to_file(file_path, imp.module_path, imp.is_relative, scanned_files)
                if target_file:
                    target_node_id = f"file:{target_file}"
                    self.g.add_edge(file_node_id, target_node_id, relation="imports", line=imp.line_number)

            # Resolve Call Sites
            for call in info.calls:
                # Caller Node ID
                if call.caller:
                    caller_node_id = f"symbol:{file_path}:{call.caller}"
                else:
                    caller_node_id = f"file:{file_path}"
                    
                if not self.g.has_node(caller_node_id):
                    # Fallback to file node if caller symbol doesn't exist
                    caller_node_id = f"file:{file_path}"

                # Try to resolve callee node
                resolved = False
                
                # Heuristic 1: Local definition
                local_sym_name = f"{call.caller.split('.')[0]}.{call.callee}" if call.caller and '.' in call.caller else call.callee
                local_sym_node_id = f"symbol:{file_path}:{local_sym_name}"
                if self.g.has_node(local_sym_node_id):
                    self.g.add_edge(caller_node_id, local_sym_node_id, relation="calls", line=call.line_number)
                    resolved = True
                    
                # Also try direct global name in same file
                global_sym_node_id = f"symbol:{file_path}:{call.callee}"
                if not resolved and self.g.has_node(global_sym_node_id):
                    self.g.add_edge(caller_node_id, global_sym_node_id, relation="calls", line=call.line_number)
                    resolved = True

                # Heuristic 2: Imported definition
                if not resolved:
                    # Find if callee is imported
                    matching_import = None
                    for imp in info.imports:
                        if imp.local_name == call.callee:
                            matching_import = imp
                            break
                    # E.g. from module import callee
                    if matching_import:
                        target_file = resolve_module_to_file(file_path, matching_import.module_path, matching_import.is_relative, scanned_files)
                        if target_file:
                            # Search in target file for exported/defined symbol matching imported name
                            imported_name = matching_import.imported_name or call.callee
                            target_sym_node_id = f"symbol:{target_file}:{imported_name}"
                            if self.g.has_node(target_sym_node_id):
                                self.g.add_edge(caller_node_id, target_sym_node_id, relation="calls", line=call.line_number)
                                resolved = True
                            else:
                                # Fallback: if we only resolved to the target file, link to the file node
                                self.g.add_edge(caller_node_id, f"file:{target_file}", relation="calls", line=call.line_number)
                                resolved = True

                # Heuristic 3: Imported module member (e.g. import os; os.path())
                # Caller call is "path()", object prefix is "os".
                # Since we only extracted callee = "path", how do we know the prefix is "os"?
                # Wait! For attribute call `obj.method()`, is_method_call is True and callee is "method".
                # If we inspect the references at the same line number inside the caller's context:
                # We can find which identifiers are used on that line!
                # If there's an identifier `obj` used on the same line, and `obj` is a known import local_name,
                # then we can resolve to that imported module!
                if not resolved and call.is_method_call:
                    # Find all identifiers referenced on this call line
                    line_refs = [ref for ref, line, scope in info.references if line == call.line_number and scope == call.caller]
                    for ref in line_refs:
                        # Check if ref is a known import
                        for imp in info.imports:
                            if imp.local_name == ref:
                                target_file = resolve_module_to_file(file_path, imp.module_path, imp.is_relative, scanned_files)
                                if target_file:
                                    target_sym_node_id = f"symbol:{target_file}:{call.callee}"
                                    if self.g.has_node(target_sym_node_id):
                                        self.g.add_edge(caller_node_id, target_sym_node_id, relation="calls", line=call.line_number)
                                        resolved = True
                                        break
                        if resolved:
                            break

                # Heuristic 4: Global method fallback
                if not resolved and call.is_method_call:
                    # Look up all classes defining call.callee method
                    candidates = self.method_lookup.get(call.callee, [])
                    for cand_file, cand_class, cand_sym in candidates:
                        cand_sym_node_id = f"symbol:{cand_file}:{cand_sym.name}"
                        if self.g.has_node(cand_sym_node_id):
                            self.g.add_edge(caller_node_id, cand_sym_node_id, relation="calls", line=call.line_number)
                            resolved = True

        logger.info(f"Symbol graph build completed. Nodes: {self.g.number_of_nodes()}, Edges: {self.g.number_of_edges()}")
        return self.g

    def to_serialized_data(self) -> SymbolGraphData:
        """Converts the NetworkX graph to a serializable SymbolGraphData model."""
        nodes_list = []
        for node_id, attrs in self.g.nodes(data=True):
            node_data = {"id": node_id}
            node_data.update(attrs)
            nodes_list.append(node_data)
            
        edges_list = []
        for u, v, attrs in self.g.edges(data=True):
            edge_data = {"source": u, "target": v}
            edge_data.update(attrs)
            edges_list.append(edge_data)
            
        return SymbolGraphData(nodes=nodes_list, edges=edges_list, smells=[])
