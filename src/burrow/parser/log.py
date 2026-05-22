import re
from burrow.parser.base import BaseParser, ParsingError
from burrow.parser.models import NormalizedError
from burrow.parser.stacktrace import get_parser, PythonStackTraceParser, JavaScriptStackTraceParser

class LogParser(BaseParser):
    """Scans log content for metadata (Docker, timestamps), strips prefixes, and extracts stack traces."""
    
    # Matches 'web_1  | ' or 'api-service-1 | '
    DOCKER_PREFIX_REGEX = re.compile(r'^[a-zA-Z0-9_\-\.]+?\s*\|\s+')
    # Matches ISO 8601 timestamps like '2026-05-22T11:32:03.123456Z '
    TIMESTAMP_PREFIX_REGEX = re.compile(r'^\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s?')
    # Matches ANSI escape sequences (e.g. colors)
    ANSI_ESCAPE_REGEX = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')
    # Matches non-printable control characters (excluding tab and newlines)
    CONTROL_CHARS_REGEX = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

    def strip_metadata(self, content: str) -> str:
        """Removes Docker compose headers, timestamp prefixes, ANSI escape sequences, and non-printable control characters line-by-line."""
        cleaned_lines = []
        for line in content.splitlines():
            line_cleaned = self.ANSI_ESCAPE_REGEX.sub("", line)
            line_cleaned = self.CONTROL_CHARS_REGEX.sub("", line_cleaned)
            line_cleaned = self.DOCKER_PREFIX_REGEX.sub("", line_cleaned)
            line_cleaned = self.TIMESTAMP_PREFIX_REGEX.sub("", line_cleaned)
            cleaned_lines.append(line_cleaned)
        return "\n".join(cleaned_lines)

    def can_parse(self, content: str) -> bool:
        cleaned = self.strip_metadata(content)
        
        py_parser = PythonStackTraceParser()
        js_parser = JavaScriptStackTraceParser()
        if py_parser.can_parse(cleaned) or js_parser.can_parse(cleaned):
            return True
            
        if "Traceback (most recent call last):" in cleaned:
            return True
        if re.search(r'\s+at\s+[\w.<>]+.*:\d+:\d+', cleaned):
            return True
            
        # Try generic parser
        try:
            from burrow.parser.generic import GenericCliParser
            if GenericCliParser().can_parse(cleaned):
                return True
        except ImportError:
            pass
            
        return False

    def parse(self, content: str) -> NormalizedError:
        cleaned = self.strip_metadata(content)
        
        # 1. Attempt to extract Python traceback block
        py_header = "Traceback (most recent call last):"
        if py_header in cleaned:
            start_idx = cleaned.find(py_header)
            subcontent = cleaned[start_idx:]
            lines = subcontent.splitlines()
            trace_lines = []
            for i, line in enumerate(lines):
                if i == 0:
                    trace_lines.append(line)
                    continue
                # Python trace frames are indented. The final line is the exception message.
                if line.startswith("  ") or "line " in line:
                    trace_lines.append(line)
                elif trace_lines and (trace_lines[-1].strip().startswith("File") or trace_lines[-1].startswith("  ")):
                    trace_lines.append(line)
                    break
                else:
                    break
            traceback_block = "\n".join(trace_lines)
            error = PythonStackTraceParser().parse(traceback_block)
            error.metadata["docker_stripped"] = (cleaned != content)
            return error
            
        # 2. Attempt to extract JS traceback block
        js_frame_pattern = re.compile(r'^\s*at\s+(?:[\w.<>]+?\s+\()?[^\s)]+?:\d+:\d+\)?')
        lines = cleaned.splitlines()
        trace_lines = []
        for i, line in enumerate(lines):
            if js_frame_pattern.match(line):
                # If we haven't collected any lines, look backward for the error message
                if not trace_lines:
                    for j in range(max(0, i-2), i):
                        val = lines[j].strip()
                        if val and (":" in val or "Error" in val) and not val.startswith("at "):
                            trace_lines.append(val)
                trace_lines.append(line)
            elif trace_lines:
                break
                
        if trace_lines:
            traceback_block = "\n".join(trace_lines)
            error = JavaScriptStackTraceParser().parse(traceback_block)
            error.metadata["docker_stripped"] = (cleaned != content)
            return error
            
        # 3. Fallback to registry parsing on the cleaned content
        try:
            parser = get_parser(cleaned)
            error = parser.parse(cleaned)
            error.metadata["docker_stripped"] = (cleaned != content)
            return error
        except ParsingError:
            raise ParsingError("Could not extract or parse any stack trace from the log content")
