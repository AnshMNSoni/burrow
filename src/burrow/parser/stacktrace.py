import re
from typing import List, Tuple, Optional
from burrow.parser.base import BaseParser, ParsingError
from burrow.parser.models import NormalizedError, NormalizedFrame

class PythonStackTraceParser(BaseParser):
    """Parser for Python traceback logs, enhanced with chained exception and Pytest support."""
    
    # Matches 'File "path/to/file.py", line 12, in function_name'
    FRAME_REGEX = re.compile(r'^\s*File\s+"(?P<file>.+?)",\s*line\s+(?P<line>\d+),\s*in\s+(?P<func>.+)')
    # Matches ExceptionType: message
    ERROR_REGEX = re.compile(r'^(?P<type>[a-zA-Z_]\w*):\s*(?P<msg>.*)$')
    # Matches Python traceback header
    HEADER_REGEX = re.compile(r'^\s*Traceback\s+\(most\s+recent\s+call\s+(?:last|first)\):')
    
    # Pytest patterns
    PYTEST_FRAME_REGEX = re.compile(r'^(?P<file>[^\s:]+\.py):(?P<line>\d+):\s*in\s+(?P<func>.+)')
    PYTEST_ERROR_REGEX = re.compile(r'^E\s+(?P<type>[a-zA-Z_]\w*):\s*(?P<msg>.*)$')

    crash_on_last_frame = True

    def can_parse(self, content: str) -> bool:
        lines = content.strip().splitlines()
        for line in lines[:5]:
            if self.HEADER_REGEX.search(line) or "in test_" in line:
                return True
        # Check for Pytest failure blocks
        if any("fail" in line.lower() or "traceback" in line.lower() for line in lines[:3]):
            if any(self.PYTEST_FRAME_REGEX.search(line) for line in lines[:20]):
                return True
        return False

    def _parse_single(self, content: str) -> NormalizedError:
        lines = [line.rstrip() for line in content.strip().splitlines()]
        frames: List[NormalizedFrame] = []
        error_type = "PythonError"
        error_msg = ""
        
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # 1. Standard python traceback frame
            match = self.FRAME_REGEX.search(line)
            if match:
                file_path = match.group("file")
                line_number = int(match.group("line"))
                func_name = match.group("func")
                
                # Check next line for source context
                code_context = None
                raw_lines = [line]
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    # Indented but not caret line like '  ^^^^^' or '  ~~~~~'
                    if next_line.startswith("    ") and not self.FRAME_REGEX.search(next_line) and not next_line.strip().startswith("^") and not next_line.strip().startswith("~"):
                        code_context = next_line.strip()
                        raw_lines.append(next_line)
                        i += 1
                
                is_app = not ("python" in file_path.lower() or "site-packages" in file_path.lower() or "lib" in file_path.lower() and not file_path.startswith("."))
                frames.append(NormalizedFrame(
                    file_path=file_path,
                    line_number=line_number,
                    function_name=func_name,
                    code_context=code_context,
                    raw_line="\n".join(raw_lines),
                    is_application_code=is_app
                ))
            
            # 2. Pytest-style frame
            else:
                pytest_match = self.PYTEST_FRAME_REGEX.match(line.strip())
                if pytest_match:
                    file_path = pytest_match.group("file")
                    line_number = int(pytest_match.group("line"))
                    func_name = pytest_match.group("func")
                    
                    code_context = None
                    raw_lines = [line]
                    # Pytest often prints the code line right above or below, or indented. Let's capture it.
                    if i + 1 < len(lines) and lines[i + 1].startswith(">"):
                        code_context = lines[i + 1].replace(">", "").strip()
                        raw_lines.append(lines[i + 1])
                        i += 1
                    
                    frames.append(NormalizedFrame(
                        file_path=file_path,
                        line_number=line_number,
                        function_name=func_name,
                        code_context=code_context,
                        raw_line="\n".join(raw_lines),
                        is_application_code=not ("site-packages" in file_path or "pytest" in file_path)
                    ))
                else:
                    # 3. Standard error type & message line
                    err_match = self.ERROR_REGEX.match(line)
                    if err_match and not self.HEADER_REGEX.search(line):
                        error_type = err_match.group("type")
                        error_msg = err_match.group("msg")
                    else:
                        # 4. Pytest error assertion line E.g. 'E   ZeroDivisionError: division by zero'
                        pytest_err = self.PYTEST_ERROR_REGEX.match(line)
                        if pytest_err:
                            error_type = pytest_err.group("type")
                            error_msg = pytest_err.group("msg")
            i += 1
            
        if not frames:
            raise ParsingError("Could not parse any frame from Python traceback content")
            
        error = NormalizedError(
            error_type=error_type,
            message=error_msg or "Unknown python exception",
            frames=frames,
            language="python",
            raw_input=content
        )
        
        # Populate heuristics
        error.surfaced_crash_point = self.get_surfaced_crash_point(error)
        error.root_origin = self.get_root_origin(error)
        error.confidence_score = self.calculate_confidence(error, content)
        return error

    def parse(self, content: str) -> NormalizedError:
        # Check for exception chaining separators
        chain_pattern = re.compile(
            r'\n(?:During handling of the above exception, another exception occurred:|'
            r'The above exception was the direct cause of the following exception:)\n'
        )
        parts = chain_pattern.split(content)
        
        if len(parts) == 1:
            return self._parse_single(parts[0])
            
        # Parse all segments
        parsed_errors = []
        for part in parts:
            try:
                parsed_errors.append(self._parse_single(part))
            except ParsingError:
                # If a sub-segment is malformed, skip it but continue with others
                continue
                
        if not parsed_errors:
            raise ParsingError("Failed to parse any segments from chained traceback")
            
        # The last segment is the final surfaced error caught at the top
        final_error = parsed_errors[-1]
        final_error.chained_errors = parsed_errors[:-1]
        return final_error


class JavaScriptStackTraceParser(BaseParser):
    """Parser for JavaScript/TypeScript stack traces, with React, Webpack and Jest support."""
    
    # Matches: '   at functionName (path/to/file.js:10:15)' or '   at path/to/file.js:10:15'
    FRAME_REGEX = re.compile(r'^\s*at\s+(?:(?P<func>[^\s(]+)\s+\()?(?P<file>[^\s)]+?):(?P<line>\d+):(?P<col>\d+)\)?')
    # Matches: 'TypeError: Cannot read properties...'
    ERROR_REGEX = re.compile(r'^(?P<type>[a-zA-Z_]\w*Error):\s*(?P<msg>.*)$')

    crash_on_last_frame = False # JS/Node stack traces print crash point first

    def can_parse(self, content: str) -> bool:
        lines = content.strip().splitlines()
        for line in lines[:5]:
            if "at " in line and (":" in line or ")" in line):
                return True
        return False

    def clean_webpack_path(self, file_path: str) -> str:
        """Strips webpack prefixes to resolve raw source file paths."""
        # E.g. webpack:///src/App.js -> src/App.js
        # webpack-internal:///./src/index.js -> src/index.js
        if "webpack-internal:///" in file_path:
            file_path = file_path.replace("webpack-internal:///", "")
        if "webpack:///" in file_path:
            file_path = file_path.replace("webpack:///", "")
        if file_path.startswith("./"):
            file_path = file_path[2:]
        return file_path

    def parse(self, content: str) -> NormalizedError:
        lines = [line.strip() for line in content.strip().splitlines() if line.strip()]
        frames: List[NormalizedFrame] = []
        error_type = "JavaScriptError"
        error_msg = ""
        
        # Parse error type and message from leading lines
        if lines:
            first_line = lines[0]
            err_match = self.ERROR_REGEX.match(first_line)
            if err_match:
                error_type = err_match.group("type")
                error_msg = err_match.group("msg")
            elif ":" in first_line and not first_line.startswith("at "):
                parts = first_line.split(":", 1)
                error_type = parts[0].strip()
                error_msg = parts[1].strip()
            else:
                error_msg = first_line
                
        for line in lines:
            match = self.FRAME_REGEX.search(line)
            if match:
                raw_file = match.group("file")
                file_path = self.clean_webpack_path(raw_file)
                line_number = int(match.group("line"))
                col_number = int(match.group("col"))
                func_name = match.group("func") or "anonymous"
                
                is_app = not ("node_modules" in file_path or "internal/" in file_path or file_path.startswith("node:") or "jest" in file_path.lower())
                
                # Check for module names in paths (e.g. from transpiled paths)
                module_name = None
                if "/" in file_path:
                    parts = file_path.split("/")
                    if len(parts) > 1 and parts[-2] != "src":
                        module_name = parts[-2]

                frames.append(NormalizedFrame(
                    file_path=file_path,
                    line_number=line_number,
                    column_number=col_number,
                    function_name=func_name,
                    module_name=module_name,
                    raw_line=line,
                    is_application_code=is_app
                ))
                
        if not frames:
            raise ParsingError("Could not parse any frame from JavaScript stack trace")
            
        error = NormalizedError(
            error_type=error_type,
            message=error_msg or "Unknown JS exception",
            frames=frames,
            language="javascript",
            raw_input=content
        )
        
        # Populate analytics
        error.surfaced_crash_point = self.get_surfaced_crash_point(error)
        error.root_origin = self.get_root_origin(error)
        error.confidence_score = self.calculate_confidence(error, content)
        
        return error


from burrow.parser.generic import GenericCliParser

# Registry of available stack trace parsers
PARSERS: List[BaseParser] = [
    PythonStackTraceParser(),
    JavaScriptStackTraceParser(),
    GenericCliParser(),
]

def get_parser(content: str) -> BaseParser:
    """Finds the first parser capable of parsing the given content."""
    for parser in PARSERS:
        if parser.can_parse(content):
            return parser
    raise ParsingError("No suitable parser found for the input content")

