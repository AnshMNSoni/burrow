import sys
import json
import argparse
from pathlib import Path
from typing import Optional

import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich import box

from burrow.config import settings
from burrow.core.engine import BurrowEngine
from burrow.utils.logging import setup_logging, logger

console = Console()
error_console = Console(stderr=True)

def read_input(file_path: Optional[str]) -> str:
    """Reads input trace data from file or stdin."""
    if not file_path or file_path == "-":
        if sys.stdin.isatty():
            raise ValueError("No input trace data provided. Pass a filepath or pipe data to stdin.")
        return sys.stdin.read()
    
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    return path.read_text(encoding="utf-8", errors="ignore")


def handle_parse(args):
    """Parses raw trace data and prints normalized JSON representation."""
    try:
        content = read_input(args.input)
        engine = BurrowEngine(project_root=args.project_root, llm_provider=args.llm_provider)
        result = engine.analyze_content(content)
        # Output raw JSON representation of NormalizedError
        print(result.error.model_dump_json(indent=2))
    except Exception as e:
        error_console.print(f"[bold red]Error:[/] {e}")
        sys.exit(1)


def print_error_stack(error, console, title_prefix=""):
    # Display Crash Summary Panel
    console.print(Panel(
        f"[bold]Exception:[/] [bold red]{error.error_type}[/]\n"
        f"[bold]Message:[/] {error.message}\n"
        f"[bold]Language:[/] [cyan]{error.language}[/]\n"
        f"[bold]Parsing Confidence:[/] [green]{error.confidence_score * 100:.0f}%[/]",
        title=f"[bold yellow]{title_prefix}Burrow Diagnostics - Ingested Failure[/]",
        border_style="red",
        box=box.ROUNDED
    ))

    # Display Root Origin if identified
    if error.root_origin:
        col_info = f":{error.root_origin.column_number}" if error.root_origin.column_number else ""
        console.print(f"\n[bold magenta]=> Estimated Root Origin of Failure:[/] [bold yellow]{error.root_origin.function_name or 'anonymous'}[/] in [cyan]{error.root_origin.file_path}:{error.root_origin.line_number}{col_info}[/]")
        if error.root_origin.code_context:
            lexer = error.language if error.language in ("python", "javascript") else "text"
            syntax = Syntax(error.root_origin.code_context, lexer, theme="monokai", line_numbers=False)
            console.print(Panel(syntax, title="[bold magenta]Root Origin Code Context[/]", border_style="magenta", box=box.ROUNDED))

    # Display Call Stack
    console.print("\n[bold cyan]=== CALL STACK (Propagation Chain) ===[/]")
    for idx, frame in enumerate(error.frames):
        app_tag = "[bold green][AppCode][/]" if frame.is_application_code else "[dim][System][/]"
        is_origin = " [bold magenta](Root Origin Culprit)[/]" if error.root_origin and frame.file_path == error.root_origin.file_path and frame.line_number == error.root_origin.line_number else ""
        col_info = f":{frame.column_number}" if frame.column_number else ""
        
        git_status = frame.metadata.get("git_status")
        git_tag = f" [bold red]({git_status.capitalize()} in Git)[/]" if git_status else ""
        
        console.print(f"\n#{idx+1} {app_tag} [bold yellow]{frame.function_name or 'anonymous'}[/] in [cyan]{frame.file_path}:{frame.line_number}{col_info}[/]{is_origin}{git_tag}")
        
        if frame.code_context:
            lexer = error.language if error.language in ("python", "javascript") else "text"
            syntax = Syntax(frame.code_context, lexer, theme="monokai", line_numbers=False)
            console.print(Panel(syntax, border_style="dim", box=box.SQUARE))
        else:
            console.print(f"    {frame.raw_line}")


def handle_analyze(args):
    """Processes traceback, resolves source contexts, builds graph and renders suggestions."""
    try:
        setup_logging(args.log_level, log_format="console")
        content = read_input(args.input)
        engine = BurrowEngine(project_root=args.project_root, llm_provider=args.llm_provider)
        result = engine.analyze_content(content)

        if args.format == "json":
            print(result.model_dump_json(indent=2))
            return

        # Display Workspace Banner if workspace context is present
        if result.workspace_context:
            ws = result.workspace_context
            fw_list = ", ".join(ws.structure.detected_frameworks) or "None detected"
            branch_info = ""
            changes_info = ""
            if ws.git:
                branch_info = f", Git Branch: [bold cyan]{ws.git.active_branch}[/]"
                mod_count = sum(1 for c in ws.git.recent_changes if c.status in ("modified", "untracked", "added", "deleted"))
                changes_info = f", Pending Changes: [bold red]{mod_count} files[/]"
            
            console.print(Panel(
                f"[bold]Detected Frameworks:[/] {fw_list}{branch_info}{changes_info}",
                title="[bold blue]Burrow Workspace Context[/]",
                border_style="blue",
                box=box.ROUNDED
            ))

        error = result.error
        rec = result.recommendation

        console.print()
        # 1. Print Chained Exception Predecessors
        for idx, chained in enumerate(error.chained_errors):
            print_error_stack(chained, console, title_prefix=f"Chained Exception #{idx+1} (Cause) - ")
            console.print("\n[bold yellow]----------------- [Propagation boundary] -----------------[/]\n")

        # 2. Print Surfaced Error
        surfaced_prefix = "Surfaced Exception - " if error.chained_errors else ""
        print_error_stack(error, console, title_prefix=surfaced_prefix)

        # 3. Display AI Recommendation Panel
        conf_color = "green" if rec.confidence > 0.8 else "yellow" if rec.confidence > 0.5 else "red"
        console.print()
        console.print(Panel(
            f"[bold green]ANALYZED CAUSE[/]\n"
            f"{rec.cause}\n\n"
            f"[bold green]RECOMMENDED ACTION[/]\n"
            f"{rec.remediation}\n\n"
            f"[bold]Confidence Score:[/] [{conf_color}]{rec.confidence * 100:.1f}%[/]",
            title="[bold yellow]AI Root Cause Remediation Plan[/]",
            border_style="yellow",
            box=box.ROUNDED
        ))
        console.print()

    except Exception as e:
        error_console.print(f"[bold red]Error:[/] {e}")
        sys.exit(1)


def handle_scan(args):
    """Performs static analysis scan of local project workspace and outputs a formatted JSON representation."""
    try:
        setup_logging(args.log_level, log_format="console")
        engine = BurrowEngine(project_root=args.project_root, llm_provider=args.llm_provider)
        # Scan the project workspace
        logger.info("Initializing workspace intelligence scan...")
        workspace_context = engine.workspace_scanner.scan()
        print(workspace_context.model_dump_json(indent=2))
    except Exception as e:
        error_console.print(f"[bold red]Error:[/] {e}")
        sys.exit(1)


def handle_api(args):
    """Starts the FastAPI Web Service."""
    setup_logging(args.log_level, log_format="console")
    logger.info(f"Starting Burrow API web server at {args.host}:{args.port}")
    uvicorn.run("burrow.api.app:app", host=args.host, port=args.port, reload=args.reload)


def main():
    parser = argparse.ArgumentParser(
        prog="burrow",
        description="Burrow: Local-first AI-powered debugging engine CLI"
    )
    parser.add_argument("--project-root", "-r", default=settings.project_root, help="Project source root path")
    parser.add_argument("--log-level", default=settings.log_level, help="Log level (debug, info, warning, error)")
    parser.add_argument("--llm-provider", default=settings.llm_provider, help="LLM integration provider (mock, ollama)")
    
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommands")

    # parse subcommand
    parse_parser = subparsers.add_parser("parse", help="Parse traceback into normalized JSON structures")
    parse_parser.add_argument("input", nargs="?", default="-", help="Path to raw trace file, or '-' for stdin")
    parse_parser.set_defaults(func=handle_parse)

    # analyze subcommand
    analyze_parser = subparsers.add_parser("analyze", help="Fully analyze traceback and display diagnostic report")
    analyze_parser.add_argument("input", nargs="?", default="-", help="Path to raw trace file, or '-' for stdin")
    analyze_parser.add_argument("--format", "-f", choices=["text", "json"], default="text", help="Report display output format")
    analyze_parser.set_defaults(func=handle_analyze)

    # scan subcommand
    scan_parser = subparsers.add_parser("scan", help="Scan local project workspace and output repository intelligence metadata")
    scan_parser.set_defaults(func=handle_scan)

    # api subcommand
    api_parser = subparsers.add_parser("api", help="Start FastAPI service interface")
    api_parser.add_argument("--host", default=settings.api_host, help="Bind Host IP Address")
    api_parser.add_argument("--port", type=int, default=settings.api_port, help="Bind Host Port Number")
    api_parser.add_argument("--reload", action="store_true", help="Start server in live-reload mode")
    api_parser.set_defaults(func=handle_api)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
