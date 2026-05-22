import re
from typing import List
from burrow.parser.base import BaseParser, ParsingError
from burrow.parser.models import NormalizedError, NormalizedFrame

class GenericCliParser(BaseParser):
    """Parser for generic toolchain or CLI errors (e.g. GCC/Clang, Go compiler, TS compiler)."""
    
    # Matches file, line, and optional column patterns
    FILE_LINE_COL_REGEX = re.compile(
        r'^(?P<file>[a-zA-Z0-9_\-\./\\+@ ]+?)'
        r'(?:\((?P<line>\d+),(?P<col>\d+)\)|:(?P<line2>\d+)(?::(?P<col2>\d+))?)'
    )
    
    # Matches common error type keywords followed by separator and the rest of the message
    TYPE_MSG_REGEX = re.compile(
        r'^(?P<type>error\s+TS\d+|TS\d+|error|warning|fatal\s+error)\b\s*[:\-]?\s*(?P<msg>.*)$',
        re.IGNORECASE
    )

    # CLI errors typically print target/crashing line first
    crash_on_last_frame: bool = False

    def can_parse(self, content: str) -> bool:
        lines = content.strip().splitlines()
        for line in lines[:5]:
            if self.FILE_LINE_COL_REGEX.match(line.strip()):
                return True
        return False

    def parse(self, content: str) -> NormalizedError:
        lines = content.strip().splitlines()
        frames: List[NormalizedFrame] = []
        error_type = "CLIError"
        error_msg = ""
        
        for line in lines:
            match = self.FILE_LINE_COL_REGEX.match(line.strip())
            if match:
                file_path = match.group("file").strip()
                line_val = match.group("line") or match.group("line2")
                col_val = match.group("col") or match.group("col2")
                
                line_num = int(line_val) if line_val else None
                col_num = int(col_val) if col_val else None
                
                is_app = not any(part in file_path for part in ("node_modules", "vendor", "sdk", "usr/lib"))
                
                frames.append(NormalizedFrame(
                    file_path=file_path,
                    line_number=line_num,
                    column_number=col_num,
                    raw_line=line,
                    is_application_code=is_app
                ))
                
                if not error_msg:
                    remaining = line.strip()[match.end():]
                    # strip leading separators ': ', ' - ' or similar
                    rest = re.sub(r'^[ :\-]+', '', remaining)
                    
                    type_match = self.TYPE_MSG_REGEX.match(rest)
                    if type_match:
                        error_type = type_match.group("type").strip()
                        error_msg = type_match.group("msg").strip()
                    else:
                        error_type = "CLIError"
                        error_msg = rest.strip()
                    
        if not frames:
            raise ParsingError("Could not parse any frame using Generic CLI pattern")
            
        error = NormalizedError(
            error_type=error_type,
            message=error_msg,
            frames=frames,
            language="generic",
            raw_input=content
        )
        
        error.surfaced_crash_point = self.get_surfaced_crash_point(error)
        error.root_origin = self.get_root_origin(error)
        error.confidence_score = self.calculate_confidence(error, content)
        
        return error
