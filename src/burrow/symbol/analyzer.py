import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple
import networkx as nx

from tree_sitter import Language, Parser
import tree_sitter_python as tspython
import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tstypescript

from burrow.symbol.models import SymbolDetail, SymbolType, CodeSmell
from burrow.symbol.graph import SymbolGraphBuilder
from burrow.utils.logging import logger

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjs.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())

class VulnerabilityWalker:
    """Walks the AST of a file to detect specific expressions like dereferences, divisions, subscripts, and loose comparisons."""
    def __init__(self, code_bytes: bytes, file_path: str, is_js: bool):
        self.code_bytes = code_bytes
        self.file_path = file_path
        self.is_js = is_js
        
        # Lists of tuples:
        # dereferences: (object_name, line_number, scope)
        self.dereferences: List[Tuple[str, int, Optional[str]]] = []
        # divisions: (denominator_name, line_number, scope)
        self.divisions: List[Tuple[str, int, Optional[str]]] = []
        # subscripts: (array_name, index_name, line_number, scope)
        self.subscripts: List[Tuple[str, str, int, Optional[str]]] = []
        # loose_comparisons: (line_number, scope)
        self.loose_comparisons: List[Tuple[int, Optional[str]]] = []
        
        self.scope_stack: List[str] = []

    def _node_text(self, node) -> str:
        return self.code_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')

    def get_scope_name(self) -> Optional[str]:
        return ".".join(self.scope_stack) if self.scope_stack else None

    def walk(self, node):
        node_type = node.type

        # Scope tracking
        is_scope = False
        if node_type in ('class_definition', 'class_declaration'):
            name_node = node.child_by_field_name('name')
            if name_node:
                self.scope_stack.append(self._node_text(name_node))
                is_scope = True
        elif node_type in ('function_definition', 'function_declaration', 'generator_function_declaration', 'method_definition'):
            name_node = node.child_by_field_name('name')
            if name_node:
                self.scope_stack.append(self._node_text(name_node))
                is_scope = True
        elif node_type == 'variable_declarator':
            name_node = node.child_by_field_name('name')
            value_node = node.child_by_field_name('value')
            if name_node and name_node.type == 'identifier' and value_node and value_node.type in ('arrow_function', 'function_expression'):
                self.scope_stack.append(self._node_text(name_node))
                is_scope = True

        # 1. Null dereference detector
        if not self.is_js and node_type == 'attribute':
            obj_node = node.child_by_field_name('object')
            if obj_node and obj_node.type == 'identifier':
                obj_name = self._node_text(obj_node)
                self.dereferences.append((obj_name, node.start_point[0] + 1, self.get_scope_name()))
        elif self.is_js and node_type == 'member_expression':
            # In JS, member_expression covers obj.prop and obj[prop]
            # Check if optional chaining is used (i.e. has ?. operator)
            has_optional = False
            for child in node.children:
                if child.type == '?.':
                    has_optional = True
                    break
            if not has_optional:
                obj_node = node.child_by_field_name('object')
                if obj_node and obj_node.type == 'identifier':
                    obj_name = self._node_text(obj_node)
                    self.dereferences.append((obj_name, node.start_point[0] + 1, self.get_scope_name()))

        # 2. Division zero-check detector
        elif not self.is_js and node_type == 'binary_operator':
            # Python operator check
            op_node = node.children[1] if len(node.children) > 1 else None
            if op_node and op_node.type in ('/', '//'):
                right_node = node.child_by_field_name('right')
                if right_node and right_node.type == 'identifier':
                    self.divisions.append((self._node_text(right_node), node.start_point[0] + 1, self.get_scope_name()))
        elif self.is_js and node_type == 'binary_expression':
            # JS operator check
            op_node = None
            for child in node.children:
                if child.type == '/':
                    op_node = child
                    break
            if op_node:
                right_node = node.child_by_field_name('right')
                if right_node and right_node.type == 'identifier':
                    self.divisions.append((self._node_text(right_node), node.start_point[0] + 1, self.get_scope_name()))

        # 3. Subscript index detector
        elif not self.is_js and node_type == 'subscript':
            # Python array lookup: value[subscript]
            val_node = node.child_by_field_name('value')
            sub_node = node.child_by_field_name('subscript')
            if val_node and val_node.type == 'identifier' and sub_node and sub_node.type == 'identifier':
                self.subscripts.append((self._node_text(val_node), self._node_text(sub_node), node.start_point[0] + 1, self.get_scope_name()))
        elif self.is_js and node_type == 'member_expression':
            # JS subscript lookup: value[index] (modeled as member_expression where property is bracketed/computed)
            # If property is bracketed, tree-sitter typically lists '[' and ']' children
            has_brackets = False
            for child in node.children:
                if child.type in ('[', ']'):
                    has_brackets = True
                    break
            if has_brackets:
                val_node = node.child_by_field_name('object')
                prop_node = node.child_by_field_name('property')
                if val_node and val_node.type == 'identifier' and prop_node and prop_node.type == 'identifier':
                    self.subscripts.append((self._node_text(val_node), self._node_text(prop_node), node.start_point[0] + 1, self.get_scope_name()))

        # 4. Loose comparison detector
        elif self.is_js and node_type == 'binary_expression':
            op_node = None
            for child in node.children:
                if child.type in ('==', '!='):
                    op_node = child
                    break
            if op_node:
                self.loose_comparisons.append((node.start_point[0] + 1, self.get_scope_name()))

        for child in node.children:
            self.walk(child)

        if is_scope:
            self.scope_stack.pop()


class SymbolGraphAnalyzer:
    def __init__(self, project_root: Path, builder: SymbolGraphBuilder):
        self.project_root = Path(project_root).resolve()
        self.builder = builder
        self.g = builder.g
        self.file_info_map = builder.file_info_map
        self.smells: List[CodeSmell] = []

    def analyze(self) -> List[CodeSmell]:
        """Runs all code smell checks and returns the list of detected vulnerabilities."""
        logger.info("Executing codebase symbol graph analysis...")
        self.smells.clear()
        
        self._check_circular_dependencies()
        self._check_broken_references()
        self._check_vulnerabilities()
        
        logger.info(f"Analysis complete. Total codebase smells detected: {len(self.smells)}")
        return self.smells

    def _check_circular_dependencies(self):
        """Detects circular import cycles using NetworkX simple_cycles."""
        file_nodes = [n for n, attr in self.g.nodes(data=True) if attr.get("type") == "file"]
        import_subgraph = nx.DiGraph()
        import_subgraph.add_nodes_from(file_nodes)
        
        for u, v, attr in self.g.edges(data=True):
            if attr.get("relation") == "imports":
                import_subgraph.add_edge(u, v)
                
        cycles = list(nx.simple_cycles(import_subgraph))
        for cycle in cycles:
            # Format circular path
            paths = [self.g.nodes[node]["path"] for node in cycle]
            cycle_str = " -> ".join(paths) + " -> " + paths[0]
            
            self.smells.append(CodeSmell(
                smell_type="circular_dependency",
                message=f"Circular dependency cycle: {cycle_str}",
                file_path=paths[0],
                line_number=1,
                severity="error"
            ))

    def _check_broken_references(self):
        """Audits all identifier references in a function/class scope for undefined/unimported bindings."""
        python_builtins = {
            "print", "len", "range", "dict", "list", "set", "str", "int", "float", 
            "enumerate", "zip", "sum", "min", "max", "any", "all", "map", "filter", 
            "open", "Exception", "ValueError", "TypeError", "KeyError", "IndexError", 
            "RuntimeError", "self", "cls", "None", "True", "False", "__name__", 
            "__file__", "super", "dir", "id", "abs", "round", "next", "iter", 
            "locals", "globals", "getattr", "setattr", "hasattr", "isinstance", 
            "issubclass", "object", "type"
        }
        
        js_builtins = {
            "console", "window", "document", "process", "module", "exports", 
            "require", "setTimeout", "clearTimeout", "setInterval", "clearInterval", 
            "Math", "JSON", "Object", "Array", "String", "Number", "Boolean", 
            "Error", "Map", "Set", "Promise", "undefined", "null", "NaN", "Infinity", 
            "arguments", "global", "this", "window", "globalThis"
        }
        
        for file_path, info in self.file_info_map.items():
            ext = Path(file_path).suffix.lower()
            is_js = ext in (".js", ".jsx", ".ts", ".tsx")
            builtins = js_builtins if is_js else python_builtins
            
            global_sym_names = {sym.name for sym in info.symbols if sym.type != SymbolType.METHOD}
            imported_names = {imp.local_name for imp in info.imports}
            
            for ref, line_num, scope in info.references:
                if ref in builtins:
                    continue
                if scope and ref in info.local_defs.get(scope, set()):
                    continue
                if ref in global_sym_names:
                    continue
                if ref in imported_names:
                    continue
                
                # Check dotted prefix e.g. os.path where os is imported
                root_part = ref.split('.')[0]
                if root_part in imported_names or root_part in global_sym_names:
                    continue
                if scope and root_part in info.local_defs.get(scope, set()):
                    continue
                    
                if is_js and ref in ("any", "string", "number", "boolean", "void", "unknown", "never", "Record", "Partial", "Promise"):
                    continue
                    
                self.smells.append(CodeSmell(
                    smell_type="broken_reference",
                    message=f"Reference to undefined or unimported symbol '{ref}'",
                    file_path=file_path,
                    line_number=line_num,
                    severity="error"
                ))

    def _check_vulnerabilities(self):
        """Performs AST walking on files to find null dereferences, missing zero/bounds checks, and loose comparisons."""
        for file_path, info in self.file_info_map.items():
            abs_path = self.project_root / file_path
            if not abs_path.exists():
                continue
                
            try:
                with open(abs_path, "rb") as f:
                    code_bytes = f.read()
                    
                file_lines = code_bytes.decode('utf-8', errors='ignore').splitlines()
                
                ext = abs_path.suffix.lower()
                is_js = ext in (".js", ".jsx", ".ts", ".tsx")
                
                parser = Parser(JS_LANGUAGE if is_js else (TS_LANGUAGE if ext == ".ts" else (TSX_LANGUAGE if ext == ".tsx" else PY_LANGUAGE)))
                tree = parser.parse(code_bytes)
                
                walker = VulnerabilityWalker(code_bytes, file_path, is_js)
                walker.walk(tree.root_node)
                
                # 1. Null dereferences
                for obj, line_num, scope in walker.dereferences:
                    # Ignore common safe names
                    if obj in ("self", "this", "console", "process", "window", "document", "Math", "JSON"):
                        continue
                        
                    # Retrieve preceding lines in scope to check for guard
                    preceding = self._get_preceding_lines(file_lines, file_path, scope, line_num)
                    
                    # Heuristics for null guards
                    is_guarded = False
                    
                    # Check same line optional chaining or short circuit
                    current_line_text = file_lines[line_num - 1] if line_num <= len(file_lines) else ""
                    if "?." in current_line_text or f"{obj} &&" in current_line_text:
                        is_guarded = True
                        
                    if not is_guarded:
                        # Check preceding lines for "if obj", "if (obj)", "if obj is not None", etc.
                        guard_patterns = [
                            re.compile(rf"\bif\b.*\b{re.escape(obj)}\b"),
                            re.compile(rf"\b{re.escape(obj)}\b.*\?"),
                            re.compile(rf"\b{re.escape(obj)}\b.*&&")
                        ]
                        for line in preceding:
                            if any(p.search(line) for p in guard_patterns):
                                is_guarded = True
                                break
                                
                    if not is_guarded:
                        self.smells.append(CodeSmell(
                            smell_type="null_dereference",
                            message=f"Dereference of object '{obj}' without safety guard or null check",
                            file_path=file_path,
                            line_number=line_num,
                            severity="warning"
                        ))
                        
                # 2. Division zero-checks
                for denom, line_num, scope in walker.divisions:
                    # Constants / literals are excluded (extractor only outputs identifier denoms)
                    preceding = self._get_preceding_lines(file_lines, file_path, scope, line_num)
                    
                    is_guarded = False
                    guard_patterns = [
                        re.compile(rf"\bif\b.*\b{re.escape(denom)}\b.*(!=|!==|>|<|is not None)"),
                        re.compile(rf"\bif\b.*\b{re.escape(denom)}\b")  # truthiness check
                    ]
                    for line in preceding:
                        if any(p.search(line) for p in guard_patterns):
                            is_guarded = True
                            break
                            
                    if not is_guarded:
                        self.smells.append(CodeSmell(
                            smell_type="missing_guard",
                            message=f"Division by variable '{denom}' without preceding zero-value verification",
                            file_path=file_path,
                            line_number=line_num,
                            severity="warning"
                        ))
                        
                # 3. Subscript bounds-checks
                for arr, idx, line_num, scope in walker.subscripts:
                    preceding = self._get_preceding_lines(file_lines, file_path, scope, line_num)
                    
                    is_guarded = False
                    guard_patterns = [
                        re.compile(rf"\b{re.escape(idx)}\b.*(<|<=|>=|>|len|length|size)"),
                        re.compile(rf"\b(len|length|size|range)\b.*\b{re.escape(idx)}\b")
                    ]
                    for line in preceding:
                        if any(p.search(line) for p in guard_patterns):
                            is_guarded = True
                            break
                            
                    if not is_guarded:
                        self.smells.append(CodeSmell(
                            smell_type="missing_guard",
                            message=f"Array subscript access '{arr}[{idx}]' without preceding bounds check on index '{idx}'",
                            file_path=file_path,
                            line_number=line_num,
                            severity="warning"
                        ))

                # 4. Loose comparison warning in JS/TS
                for line_num, scope in walker.loose_comparisons:
                    self.smells.append(CodeSmell(
                        smell_type="loose_comparison",
                        message="Loose comparison (== or !=) used; prefer strict equality (=== or !==) in Javascript/Typescript",
                        file_path=file_path,
                        line_number=line_num,
                        severity="info"
                    ))

            except Exception as e:
                logger.error(f"Failed vulnerability checks on {file_path}: {e}")

    def _get_preceding_lines(self, file_lines: List[str], file_path: str, scope: Optional[str], current_line: int) -> List[str]:
        """Extracts lines preceding current_line within the same scope boundary."""
        if not scope:
            # Top level file scope
            return file_lines[0 : max(0, current_line - 1)]
            
        # Find scope symbol details
        scope_sym = None
        info = self.file_info_map.get(file_path)
        if info:
            for sym in info.symbols:
                if sym.name == scope:
                    scope_sym = sym
                    break
                    
        if scope_sym:
            start = max(0, scope_sym.start_line - 1)
            end = max(0, current_line - 1)
            return file_lines[start:end]
            
        return file_lines[0 : max(0, current_line - 1)]
