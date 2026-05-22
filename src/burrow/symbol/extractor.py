import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple
from pydantic import BaseModel, Field

from tree_sitter import Language, Parser
import tree_sitter_python as tspython
import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tstypescript

from burrow.symbol.models import SymbolDetail, SymbolType, CallSiteDetail
from burrow.utils.logging import logger

# Initialize Tree-sitter languages (reused from scanner or redefined here)
PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjs.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())

class ImportBinding(BaseModel):
    local_name: str
    module_path: str
    imported_name: Optional[str] = None
    is_relative: bool = False
    line_number: int

class ExportBinding(BaseModel):
    name: str
    line_number: int
    is_default: bool = False

class ExtractedFileInfo(BaseModel):
    file_path: str
    symbols: List[SymbolDetail] = Field(default_factory=list)
    calls: List[CallSiteDetail] = Field(default_factory=list)
    imports: List[ImportBinding] = Field(default_factory=list)
    exports: List[ExportBinding] = Field(default_factory=list)
    references: List[Tuple[str, int, Optional[str]]] = Field(default_factory=list)  # (identifier, line_number, scope)
    local_defs: Dict[str, Set[str]] = Field(default_factory=dict)  # scope -> set of names


class PythonWalker:
    def __init__(self, code_bytes: bytes, file_path: str):
        self.code_bytes = code_bytes
        self.file_path = file_path
        self.symbols: List[SymbolDetail] = []
        self.calls: List[CallSiteDetail] = []
        self.imports: List[ImportBinding] = []
        self.exports: List[ExportBinding] = []
        self.references: List[Tuple[str, int, Optional[str]]] = []
        self.local_defs: Dict[str, Set[str]] = {}

        self.scope_stack: List[str] = []
        self.current_class: Optional[str] = None

    def _node_text(self, node) -> str:
        return self.code_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')

    def get_scope_name(self) -> Optional[str]:
        return ".".join(self.scope_stack) if self.scope_stack else None

    def _add_local_def(self, name: str):
        scope = self.get_scope_name()
        if scope:
            if scope not in self.local_defs:
                self.local_defs[scope] = set()
            self.local_defs[scope].add(name)

    def _extract_identifiers(self, node) -> List[str]:
        if not node:
            return []
        if node.type == 'identifier':
            return [self._node_text(node)]
        names = []
        for child in node.children:
            names.extend(self._extract_identifiers(child))
        return names

    def _get_docstring(self, node) -> Optional[str]:
        if not node or node.type != 'block':
            return None
        first_stmt = node.children[0] if node.children else None
        if first_stmt and first_stmt.type == 'expression_statement':
            string_node = first_stmt.children[0] if first_stmt.children else None
            if string_node and string_node.type == 'string':
                text = self._node_text(string_node)
                return text.strip('"""\'\'\'')
        return None

    def _parse_aliased_import(self, node) -> Tuple[str, str]:
        dotted_name_node = None
        alias_node = None
        for child in node.children:
            if child.type == 'dotted_name':
                dotted_name_node = child
            elif child.type == 'identifier':
                alias_node = child
        if dotted_name_node and alias_node:
            return self._node_text(alias_node), self._node_text(dotted_name_node)
        return "", ""

    def _is_reference_identifier(self, node) -> bool:
        if node.type != 'identifier':
            return False
        parent = node.parent
        if not parent:
            return True

        if parent.type == 'function_definition' and parent.child_by_field_name('name') == node:
            return False
        if parent.type == 'class_definition' and parent.child_by_field_name('name') == node:
            return False
        if parent.type == 'parameters' or (parent.parent and parent.parent.type == 'parameters'):
            return False
        if parent.type == 'attribute' and parent.child_by_field_name('attribute') == node:
            return False
        if parent.type == 'keyword_argument' and parent.child_by_field_name('name') == node:
            return False

        return True

    def walk(self, node):
        node_type = node.type

        # Handle class definitions
        if node_type == 'class_definition':
            name_node = node.child_by_field_name('name')
            if name_node:
                class_name = self._node_text(name_node)
                body_node = node.child_by_field_name('body')
                docstring = self._get_docstring(body_node)
                
                sig_end = body_node.start_byte if body_node else node.end_byte
                signature = self.code_bytes[node.start_byte:sig_end].decode('utf-8', errors='ignore').strip().rstrip(':')

                self.symbols.append(SymbolDetail(
                    name=class_name,
                    type=SymbolType.CLASS,
                    file_path=self.file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    docstring=docstring,
                    signature=signature
                ))

                old_class = self.current_class
                self.current_class = class_name
                self.scope_stack.append(class_name)

                # Process class body/children
                for child in node.children:
                    self.walk(child)

                self.scope_stack.pop()
                self.current_class = old_class
                return

        # Handle function/method definitions
        elif node_type == 'function_definition':
            name_node = node.child_by_field_name('name')
            if name_node:
                func_name = self._node_text(name_node)
                body_node = node.child_by_field_name('body')
                docstring = self._get_docstring(body_node)

                sig_end = body_node.start_byte if body_node else node.end_byte
                signature = self.code_bytes[node.start_byte:sig_end].decode('utf-8', errors='ignore').strip().rstrip(':')

                symbol_type = SymbolType.METHOD if self.current_class else SymbolType.FUNCTION

                self.symbols.append(SymbolDetail(
                    name=func_name,
                    type=symbol_type,
                    file_path=self.file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    docstring=docstring,
                    signature=signature
                ))

                self.scope_stack.append(func_name)
                scope_name = self.get_scope_name()
                self.local_defs[scope_name] = set()

                # Extract parameters
                parameters_node = node.child_by_field_name('parameters')
                if parameters_node:
                    for param in parameters_node.children:
                        param_name = None
                        if param.type == 'identifier':
                            param_name = self._node_text(param)
                        elif param.type in ('typed_parameter', 'default_parameter'):
                            name_node = param.child_by_field_name('name') or param.child_by_field_name('parameter')
                            if not name_node:
                                for c in param.children:
                                    if c.type == 'identifier':
                                        name_node = c
                                        break
                            if name_node:
                                param_name = self._node_text(name_node)
                        elif param.type in ('list_splat_pattern', 'dictionary_splat_pattern'):
                            for c in param.children:
                                if c.type == 'identifier':
                                    param_name = self._node_text(c)
                                    break
                        if param_name:
                            self._add_local_def(param_name)

                # Process function body/children
                for child in node.children:
                    self.walk(child)

                self.scope_stack.pop()
                return

        # Track local variable definitions
        elif node_type == 'assignment':
            left_node = node.child_by_field_name('left')
            if left_node:
                for name in self._extract_identifiers(left_node):
                    self._add_local_def(name)
        elif node_type == 'augmented_assignment':
            left_node = node.child_by_field_name('left')
            if left_node:
                for name in self._extract_identifiers(left_node):
                    self._add_local_def(name)
        elif node_type == 'for_statement':
            left_node = node.child_by_field_name('left')
            if left_node:
                for name in self._extract_identifiers(left_node):
                    self._add_local_def(name)
        elif node_type == 'as_pattern':
            alias_node = node.child_by_field_name('alias') or (node.children[-1] if node.children else None)
            if alias_node and alias_node.type == 'identifier':
                self._add_local_def(self._node_text(alias_node))
        elif node_type == 'except_clause':
            name_node = node.child_by_field_name('name')
            if name_node and name_node.type == 'identifier':
                self._add_local_def(self._node_text(name_node))

        # Handle imports
        elif node_type == 'import_statement':
            for child in node.children:
                if child.type == 'dotted_name':
                    mod = self._node_text(child)
                    self.imports.append(ImportBinding(
                        local_name=mod.split('.')[0],
                        module_path=mod,
                        line_number=node.start_point[0] + 1
                    ))
                elif child.type in ('aliased_import', 'aliased_import_name'):
                    local_name, module_path = self._parse_aliased_import(child)
                    if local_name:
                        self.imports.append(ImportBinding(
                            local_name=local_name,
                            module_path=module_path,
                            line_number=node.start_point[0] + 1
                        ))
        elif node_type == 'import_from_statement':
            module_path = ""
            is_relative = False
            import_idx = -1
            for i, child in enumerate(node.children):
                if child.type == 'import':
                    import_idx = i
                    break

            dots = 0
            module_node = None
            for child in node.children[:import_idx]:
                if child.type == '.':
                    is_relative = True
                    dots += 1
                elif child.type == 'import_prefix':
                    is_relative = True
                    dots += self._node_text(child).count('.')
                elif child.type == 'dotted_name':
                    module_node = child
                elif child.type == 'relative_import':
                    is_relative = True
                    for sub in child.children:
                        if sub.type == 'import_prefix':
                            dots += self._node_text(sub).count('.')
                        elif sub.type == '.':
                            dots += 1
                        elif sub.type == 'dotted_name':
                            module_node = sub

            if module_node:
                module_path = self._node_text(module_node)
            if is_relative:
                module_path = "." * dots + module_path

            import_items = []
            for child in node.children[import_idx + 1:]:
                if child.type == 'import_list':
                    import_items.extend(child.children)
                else:
                    import_items.append(child)

            for item in import_items:
                if item.type in ('identifier', 'dotted_name'):
                    name = self._node_text(item)
                    self.imports.append(ImportBinding(
                        local_name=name,
                        module_path=module_path,
                        imported_name=name,
                        is_relative=is_relative,
                        line_number=node.start_point[0] + 1
                    ))
                elif item.type in ('aliased_import', 'aliased_import_name'):
                    local_name, imported_name = self._parse_aliased_import(item)
                    if local_name:
                        self.imports.append(ImportBinding(
                            local_name=local_name,
                            module_path=module_path,
                            imported_name=imported_name,
                            is_relative=is_relative,
                            line_number=node.start_point[0] + 1
                        ))

        # Handle call sites
        elif node_type == 'call':
            func_node = node.child_by_field_name('function')
            if func_node:
                callee = ""
                is_method_call = False
                if func_node.type == 'identifier':
                    callee = self._node_text(func_node)
                elif func_node.type == 'attribute':
                    attr_node = func_node.child_by_field_name('attribute')
                    if attr_node:
                        callee = self._node_text(attr_node)
                        is_method_call = True
                if callee:
                    self.calls.append(CallSiteDetail(
                        caller=self.get_scope_name(),
                        callee=callee,
                        file_path=self.file_path,
                        line_number=node.start_point[0] + 1,
                        is_method_call=is_method_call
                    ))

        # Track references to external / undefined / nullable things
        elif node_type == 'identifier':
            if self._is_reference_identifier(node):
                self.references.append((self._node_text(node), node.start_point[0] + 1, self.get_scope_name()))

        for child in node.children:
            self.walk(child)


class JavaScriptWalker:
    def __init__(self, code_bytes: bytes, file_path: str):
        self.code_bytes = code_bytes
        self.file_path = file_path
        self.symbols: List[SymbolDetail] = []
        self.calls: List[CallSiteDetail] = []
        self.imports: List[ImportBinding] = []
        self.exports: List[ExportBinding] = []
        self.references: List[Tuple[str, int, Optional[str]]] = []
        self.local_defs: Dict[str, Set[str]] = {}

        self.scope_stack: List[str] = []
        self.current_class: Optional[str] = None

    def _node_text(self, node) -> str:
        return self.code_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')

    def get_scope_name(self) -> Optional[str]:
        return ".".join(self.scope_stack) if self.scope_stack else None

    def _add_local_def(self, name: str):
        scope = self.get_scope_name()
        if scope:
            if scope not in self.local_defs:
                self.local_defs[scope] = set()
            self.local_defs[scope].add(name)

    def _extract_identifiers(self, node) -> List[str]:
        if not node:
            return []
        if node.type in ('identifier', 'shorthand_property_identifier', 'shorthand_property_identifier_pattern'):
            return [self._node_text(node)]
        names = []
        for child in node.children:
            names.extend(self._extract_identifiers(child))
        return names

    def _get_js_docstring(self, node) -> Optional[str]:
        prev = node.prev_sibling
        if prev and prev.type == 'comment':
            text = self._node_text(prev)
            if text.startswith('/*'):
                return text.strip('/* \n\t*').strip()
        return None

    def _parse_js_import_specifier(self, node) -> Tuple[str, str]:
        name_node = node.child_by_field_name('name')
        alias_node = node.child_by_field_name('alias')
        if name_node:
            imported_name = self._node_text(name_node)
            local_name = self._node_text(alias_node) if alias_node else imported_name
            return local_name, imported_name
            
        identifiers = [c for c in node.children if c.type == 'identifier']
        if len(identifiers) == 2:
            return self._node_text(identifiers[1]), self._node_text(identifiers[0])
        elif len(identifiers) == 1:
            name = self._node_text(identifiers[0])
            return name, name
        return "", ""

    def _is_reference_identifier(self, node) -> bool:
        if node.type != 'identifier':
            return False
        parent = node.parent
        if not parent:
            return True

        if parent.type == 'function_declaration' and parent.child_by_field_name('name') == node:
            return False
        if parent.type == 'class_declaration' and parent.child_by_field_name('name') == node:
            return False
        if parent.type == 'method_definition' and parent.child_by_field_name('name') == node:
            return False
        if parent.type == 'member_expression' and parent.child_by_field_name('property') == node:
            return False
        if parent.type == 'pair' and parent.child_by_field_name('key') == node:
            return False
        if parent.type == 'property_identifier':
            return False
        if parent.type == 'shorthand_property_identifier':
            return True

        return True

    def walk(self, node):
        node_type = node.type

        # Handle class declarations
        if node_type == 'class_declaration':
            name_node = node.child_by_field_name('name')
            if name_node:
                class_name = self._node_text(name_node)
                body_node = node.child_by_field_name('body')
                docstring = self._get_js_docstring(node)

                sig_end = body_node.start_byte if body_node else node.end_byte
                signature = self.code_bytes[node.start_byte:sig_end].decode('utf-8', errors='ignore').strip()

                self.symbols.append(SymbolDetail(
                    name=class_name,
                    type=SymbolType.CLASS,
                    file_path=self.file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    docstring=docstring,
                    signature=signature
                ))

                old_class = self.current_class
                self.current_class = class_name
                self.scope_stack.append(class_name)

                for child in node.children:
                    self.walk(child)

                self.scope_stack.pop()
                self.current_class = old_class
                return

        # Handle function declarations
        elif node_type in ('function_declaration', 'generator_function_declaration'):
            name_node = node.child_by_field_name('name')
            if name_node:
                func_name = self._node_text(name_node)
                body_node = node.child_by_field_name('body')
                docstring = self._get_js_docstring(node)

                sig_end = body_node.start_byte if body_node else node.end_byte
                signature = self.code_bytes[node.start_byte:sig_end].decode('utf-8', errors='ignore').strip()

                self.symbols.append(SymbolDetail(
                    name=func_name,
                    type=SymbolType.FUNCTION,
                    file_path=self.file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    docstring=docstring,
                    signature=signature
                ))

                self.scope_stack.append(func_name)
                scope_name = self.get_scope_name()
                self.local_defs[scope_name] = set()

                # Parameters
                params_node = node.child_by_field_name('parameters')
                if params_node:
                    for param in params_node.children:
                        for name in self._extract_identifiers(param):
                            self._add_local_def(name)

                for child in node.children:
                    self.walk(child)

                self.scope_stack.pop()
                return

        # Handle arrow functions and function expressions in variable assignments
        elif node_type == 'variable_declarator':
            name_node = node.child_by_field_name('name')
            value_node = node.child_by_field_name('value')
            
            if name_node:
                for name in self._extract_identifiers(name_node):
                    self._add_local_def(name)

            if name_node and name_node.type == 'identifier' and value_node and value_node.type in ('arrow_function', 'function_expression'):
                func_name = self._node_text(name_node)
                docstring = self._get_js_docstring(node.parent if node.parent else node)
                body_node = value_node.child_by_field_name('body')

                sig_end = body_node.start_byte if body_node else value_node.end_byte
                signature = f"const {func_name} = " + self.code_bytes[value_node.start_byte:sig_end].decode('utf-8', errors='ignore').strip()

                self.symbols.append(SymbolDetail(
                    name=func_name,
                    type=SymbolType.FUNCTION,
                    file_path=self.file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    docstring=docstring,
                    signature=signature
                ))

                self.scope_stack.append(func_name)
                scope_name = self.get_scope_name()
                self.local_defs[scope_name] = set()

                # Add parameters
                params_node = value_node.child_by_field_name('parameters')
                if not params_node:
                    # E.g. x => x * 2
                    for child in value_node.children:
                        if child.type == 'identifier':
                            self._add_local_def(self._node_text(child))
                else:
                    for param in params_node.children:
                        for name in self._extract_identifiers(param):
                            self._add_local_def(name)

                for child in value_node.children:
                    self.walk(child)

                self.scope_stack.pop()
                return

        # Handle class methods
        elif node_type == 'method_definition':
            name_node = node.child_by_field_name('name')
            if name_node:
                method_name = self._node_text(name_node)
                body_node = node.child_by_field_name('body')
                docstring = self._get_js_docstring(node)

                sig_end = body_node.start_byte if body_node else node.end_byte
                signature = self.code_bytes[node.start_byte:sig_end].decode('utf-8', errors='ignore').strip()

                self.symbols.append(SymbolDetail(
                    name=method_name,
                    type=SymbolType.METHOD,
                    file_path=self.file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    docstring=docstring,
                    signature=signature
                ))

                self.scope_stack.append(method_name)
                scope_name = self.get_scope_name()
                self.local_defs[scope_name] = set()

                params_node = node.child_by_field_name('parameters')
                if params_node:
                    for param in params_node.children:
                        for name in self._extract_identifiers(param):
                            self._add_local_def(name)

                for child in node.children:
                    self.walk(child)

                self.scope_stack.pop()
                return

        # Handle imports
        elif node_type == 'import_statement':
            source_node = node.child_by_field_name('source')
            if source_node:
                module_path = self._node_text(source_node).strip("'\"")
                is_relative = module_path.startswith(".")
                
                # Check for named imports, namespace imports, or default imports
                # Let's search children
                for child in node.children:
                    if child.type == 'import_clause':
                        # Can have default import directly as identifier child
                        for sub in child.children:
                            if sub.type == 'identifier':
                                self.imports.append(ImportBinding(
                                    local_name=self._node_text(sub),
                                    module_path=module_path,
                                    imported_name="default",
                                    is_relative=is_relative,
                                    line_number=node.start_point[0]+1
                                ))
                            elif sub.type == 'named_imports':
                                for spec in sub.children:
                                    if spec.type == 'import_specifier':
                                        local_name, imported_name = self._parse_js_import_specifier(spec)
                                        if local_name:
                                            self.imports.append(ImportBinding(
                                                local_name=local_name,
                                                module_path=module_path,
                                                imported_name=imported_name,
                                                is_relative=is_relative,
                                                line_number=node.start_point[0]+1
                                            ))
                            elif sub.type == 'namespace_import':
                                # E.g. * as ns
                                for grandchild in sub.children:
                                    if grandchild.type == 'identifier':
                                        self.imports.append(ImportBinding(
                                            local_name=self._node_text(grandchild),
                                            module_path=module_path,
                                            is_relative=is_relative,
                                            line_number=node.start_point[0]+1
                                        ))

        # Handle CommonJS require and exports
        elif node_type == 'call_expression':
            func_node = node.child_by_field_name('function')
            if func_node:
                callee = ""
                is_method_call = False
                if func_node.type == 'identifier':
                    callee = self._node_text(func_node)
                    if callee == 'require':
                        # Check for require('module')
                        arg_list = node.child_by_field_name('arguments')
                        if arg_list and arg_list.children:
                            for arg in arg_list.children:
                                if arg.type in ('string', 'string_fragment', 'string_literal'):
                                    module_path = self._node_text(arg).strip("'\"")
                                    is_relative = module_path.startswith(".")
                                    # Attempt to find what variable this require binds to if its parent is variable_declarator
                                    parent = node.parent
                                    if parent and parent.type == 'variable_declarator':
                                        name_node = parent.child_by_field_name('name')
                                        if name_node:
                                            # If name is object pattern (destructuring), e.g. const { a } = require('b')
                                            for name in self._extract_identifiers(name_node):
                                                self.imports.append(ImportBinding(
                                                    local_name=name,
                                                    module_path=module_path,
                                                    imported_name=name,  # Bind as name
                                                    is_relative=is_relative,
                                                    line_number=node.start_point[0] + 1
                                                ))
                                            break
                                            
                                            if name_node.type == 'identifier':
                                                self.imports.append(ImportBinding(
                                                    local_name=self._node_text(name_node),
                                                    module_path=module_path,
                                                    is_relative=is_relative,
                                                    line_number=node.start_point[0]+1
                                                ))
                                    break
                elif func_node.type == 'member_expression':
                    prop_node = func_node.child_by_field_name('property')
                    if prop_node:
                        callee = self._node_text(prop_node)
                        is_method_call = True
                if callee and callee != 'require':
                    self.calls.append(CallSiteDetail(
                        caller=self.get_scope_name(),
                        callee=callee,
                        file_path=self.file_path,
                        line_number=node.start_point[0] + 1,
                        is_method_call=is_method_call
                    ))

        # Handle ES6 exports
        elif node_type == 'export_statement':
            is_default = False
            for child in node.children:
                if child.type == 'default':
                    is_default = True
                    break

            decl_node = None
            for child in node.children:
                if child.type in ('class_declaration', 'function_declaration', 'generator_function_declaration', 'lexical_declaration', 'variable_declaration'):
                    decl_node = child
                    break

            if decl_node:
                if decl_node.type in ('class_declaration', 'function_declaration', 'generator_function_declaration'):
                    name_node = decl_node.child_by_field_name('name')
                    if name_node:
                        name = self._node_text(name_node)
                        self.exports.append(ExportBinding(name=name, line_number=node.start_point[0]+1, is_default=is_default))
                elif decl_node.type in ('lexical_declaration', 'variable_declaration'):
                    for sub in decl_node.children:
                        if sub.type == 'variable_declarator':
                            name_node = sub.child_by_field_name('name')
                            if name_node:
                                for name in self._extract_identifiers(name_node):
                                    self.exports.append(ExportBinding(name=name, line_number=node.start_point[0]+1, is_default=is_default))
            else:
                for child in node.children:
                    if child.type == 'export_clause':
                        for spec in child.children:
                            if spec.type == 'export_specifier':
                                name_node = spec.child_by_field_name('name')
                                alias_node = spec.child_by_field_name('alias')
                                exp_name = ""
                                if alias_node:
                                    exp_name = self._node_text(alias_node)
                                elif name_node:
                                    exp_name = self._node_text(name_node)
                                else:
                                    identifiers = [c for c in spec.children if c.type == 'identifier']
                                    if len(identifiers) == 2:
                                        exp_name = self._node_text(identifiers[1])
                                    elif len(identifiers) == 1:
                                        exp_name = self._node_text(identifiers[0])
                                if exp_name:
                                    self.exports.append(ExportBinding(name=exp_name, line_number=node.start_point[0]+1, is_default=is_default))

        # Handle CommonJS exports
        elif node_type == 'assignment_expression':
            left_node = node.child_by_field_name('left')
            if left_node and left_node.type == 'member_expression':
                obj_node = left_node.child_by_field_name('object')
                prop_node = left_node.child_by_field_name('property')
                if obj_node and prop_node:
                    obj_text = self._node_text(obj_node)
                    prop_text = self._node_text(prop_node)
                    if obj_text == "module" and prop_text == "exports":
                        right_node = node.child_by_field_name('right')
                        if right_node and right_node.type == 'object':
                            for child in right_node.children:
                                if child.type == 'pair':
                                    key_node = child.child_by_field_name('key')
                                    if key_node:
                                        self.exports.append(ExportBinding(name=self._node_text(key_node), line_number=node.start_point[0]+1))
                                elif child.type == 'shorthand_property_identifier':
                                    self.exports.append(ExportBinding(name=self._node_text(child), line_number=node.start_point[0]+1))
                    elif obj_text == "exports":
                        self.exports.append(ExportBinding(name=prop_text, line_number=node.start_point[0]+1))

        # Track references
        elif node_type == 'identifier':
            if self._is_reference_identifier(node):
                self.references.append((self._node_text(node), node.start_point[0] + 1, self.get_scope_name()))

        for child in node.children:
            self.walk(child)


def extract_symbols(file_path: Path, project_root: Path) -> Optional[ExtractedFileInfo]:
    """Reads a file, parses its AST using Tree-sitter, walks the nodes, and returns ExtractedFileInfo."""
    abs_path = Path(file_path).resolve()
    if not abs_path.exists():
        return None

    ext = abs_path.suffix.lower()
    if ext not in (".py", ".js", ".jsx", ".ts", ".tsx"):
        return None

    try:
        rel_path = str(abs_path.relative_to(project_root.resolve()).as_posix())
    except ValueError:
        rel_path = str(abs_path.as_posix())

    try:
        with open(abs_path, "rb") as f:
            code_bytes = f.read()

        # Parse with Tree-sitter
        py_parser = Parser(PY_LANGUAGE)
        js_parser = Parser(JS_LANGUAGE)
        ts_parser = Parser(TS_LANGUAGE)
        tsx_parser = Parser(TSX_LANGUAGE)

        if ext == ".py":
            tree = py_parser.parse(code_bytes)
            walker = PythonWalker(code_bytes, rel_path)
        elif ext in (".js", ".jsx"):
            tree = js_parser.parse(code_bytes)
            walker = JavaScriptWalker(code_bytes, rel_path)
        elif ext == ".ts":
            tree = ts_parser.parse(code_bytes)
            walker = JavaScriptWalker(code_bytes, rel_path)
        elif ext == ".tsx":
            tree = tsx_parser.parse(code_bytes)
            walker = JavaScriptWalker(code_bytes, rel_path)
        else:
            return None

        walker.walk(tree.root_node)

        return ExtractedFileInfo(
            file_path=rel_path,
            symbols=walker.symbols,
            calls=walker.calls,
            imports=walker.imports,
            exports=walker.exports,
            references=walker.references,
            local_defs=walker.local_defs
        )
    except Exception as e:
        logger.error(f"Failed to parse and extract symbols for {file_path}: {e}")
        return None
