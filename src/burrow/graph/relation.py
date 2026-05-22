from burrow.parser.models import NormalizedError
from burrow.graph.base import Graph

def populate_error_graph(graph: Graph, error: NormalizedError) -> None:
    """Populates the graph with relationships and nodes derived from a NormalizedError."""
    # Create the core error node
    error_node_id = f"error:{error.error_type}"
    graph.add_node(error_node_id, "error", {
        "error_type": error.error_type,
        "message": error.message,
        "language": error.language
    })

    prev_frame_id = None
    
    # Process frames
    for idx, frame in enumerate(error.frames):
        # Create file node
        file_id = f"file:{frame.file_path}"
        graph.add_node(file_id, "file", {
            "path": frame.file_path,
            "is_application_code": frame.is_application_code
        })
        
        # Create frame node
        frame_id = f"frame:{frame.file_path}:{frame.line_number}:{frame.function_name}"
        graph.add_node(frame_id, "frame", {
            "file_path": frame.file_path,
            "line_number": frame.line_number,
            "function_name": frame.function_name,
            "raw_line": frame.raw_line,
            "is_application_code": frame.is_application_code,
            "code_context": frame.code_context
        })
        
        # Link File -> Frame (CONTAINS)
        graph.add_edge(file_id, frame_id, "contains")
        
        # Link Frame -> Frame (CALLS)
        if prev_frame_id:
            graph.add_edge(prev_frame_id, frame_id, "calls")
            
        prev_frame_id = frame_id

    # The last frame is the one that raised/caused the error
    if prev_frame_id:
        graph.add_edge(prev_frame_id, error_node_id, "raises")

    # Recursively link chained errors
    for chained in error.chained_errors:
        populate_error_graph(graph, chained)
        chained_node_id = f"error:{chained.error_type}"
        # The main error was caused by the chained error
        graph.add_edge(error_node_id, chained_node_id, "caused_by")

