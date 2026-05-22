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

        # Display Codebase Vulnerabilities & Warnings if present and relevant
        if result.symbol_graph_data and result.symbol_graph_data.smells:
            smells_text = []
            for smell in result.symbol_graph_data.smells:
                sev = f"[bold red]{smell.severity.upper()}[/]" if smell.severity.lower() == "error" else f"[bold yellow]{smell.severity.upper()}[/]"
                smells_text.append(
                    f"• [{sev}] [bold]{smell.smell_type}[/]: {smell.message}\n"
                    f"  in [cyan]{smell.file_path}:{smell.line_number}[/]"
                )
            if smells_text:
                console.print()
                console.print(Panel(
                    "\n\n".join(smells_text),
                    title="[bold yellow]CODEBASE VULNERABILITIES & WARNINGS[/]",
                    border_style="yellow",
                    box=box.ROUNDED
                ))

        # Display Root Cause Ranked Hypotheses & Propagation Chain
        if result.rca_result:
            rca = result.rca_result
            lines = []
            if rca.propagation_chain:
                lines.append("[bold cyan]Execution Propagation Chain:[/]")
                chain_formatted = " -> ".join(f"[yellow]{step}[/]" for step in rca.propagation_chain)
                lines.append(f"  {chain_formatted}\n")
            
            lines.append("[bold cyan]Ranked Hypotheses:[/]")
            for idx, hyp in enumerate(rca.hypotheses):
                conf_val = hyp.confidence_score * 100
                c_color = "green" if conf_val >= 85 else "yellow" if conf_val >= 60 else "red"
                
                type_badge = f"[bold white on red] {hyp.type.upper()} [/]" if hyp.type in ("config_issue", "env_mismatch", "null_reference") else \
                             f"[bold white on magenta] {hyp.type.upper()} [/]" if hyp.type in ("import_failure", "broken_reference") else \
                             f"[bold black on yellow] {hyp.type.upper()} [/]" if hyp.type in ("bad_state_propagation", "api_mismatch") else \
                             f"[bold black on green] {hyp.type.upper()} [/]" if hyp.type == "recent_change" else \
                             f"[bold white on blue] {hyp.type.upper()} [/]" if hyp.type == "async_issue" else \
                             f"[bold white on dim] {hyp.type.upper()} [/]"
                             
                origin = f"{hyp.origin_file}:{hyp.line_number}" if hyp.line_number else hyp.origin_file
                
                lines.append(
                    f"\n{idx+1}. {type_badge} [bold white]{hyp.root_cause}[/] "
                    f"([{c_color}]{conf_val:.0f}% confidence[/])\n"
                    f"   [bold]Origin File:[/] [cyan]{origin}[/]\n"
                    f"   [bold]Reasoning:[/] {hyp.reasoning_summary}\n"
                    f"   [bold]Safest Fix:[/] [green]{hyp.safest_fix_direction}[/]"
                )
                if hyp.probable_impacted_modules:
                    lines.append(f"   [bold]Impacted Modules:[/] {', '.join(hyp.probable_impacted_modules)}")
            
            console.print()
            console.print(Panel(
                "\n".join(lines),
                title="[bold yellow]AI ROOT CAUSE RANKED HYPOTHESES[/]",
                border_style="yellow",
                box=box.ROUNDED
            ))

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

        # 4. Display Fix Suggestions and Patch Previews (Stage 7)
        if result.remediation_result and result.remediation_result.suggestions:
            rem_lines = []
            for idx, sug in enumerate(result.remediation_result.suggestions):
                risk_val = sug.risk_level.lower()
                if risk_val == "safe":
                    risk_badge = "[bold black on green] SAFE [/]"
                elif risk_val == "medium":
                    risk_badge = "[bold black on yellow] MEDIUM [/]"
                else:
                    risk_badge = "[bold white on red] RISKY [/]"
                
                rem_lines.append(
                    f"[bold yellow]Suggestion #{idx+1} {risk_badge}[/]\n"
                    f"  [bold]Fix Description:[/] [white]{sug.description}[/]\n"
                    f"  [bold]Affected File:[/] [cyan]{sug.affected_file}[/]"
                )
                if sug.likely_edit_region:
                    rem_lines.append(f"  [bold]Likely Edit Region:[/] {sug.likely_edit_region}")
                
                rem_lines.append(
                    f"  [bold]Why it helps:[/] {sug.rationale}"
                )
                
                if sug.patch_preview:
                    rem_lines.append("  [bold]Patch Preview:[/]")
                    preview_lines = sug.patch_preview.split("\n")
                    formatted_preview = []
                    for line in preview_lines:
                        if line.startswith("+"):
                            formatted_preview.append(f"    [green]{line}[/]")
                        elif line.startswith("-"):
                            formatted_preview.append(f"    [red]{line}[/]")
                        else:
                            formatted_preview.append(f"    [dim]{line}[/]")
                    rem_lines.append("\n".join(formatted_preview))
                
                rem_lines.append("")
                
            console.print(Panel(
                "\n".join(rem_lines).rstrip(),
                title="[bold green]RECOMMENDED REMEDIATION & PATCH SUGGESTIONS[/]",
                border_style="green",
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


def handle_check(args):
    """Performs static symbol graph analysis on the entire workspace and displays detected issues."""
    try:
        setup_logging(args.log_level, log_format="console")
        from burrow.symbol.graph import SymbolGraphBuilder
        from burrow.symbol.analyzer import SymbolGraphAnalyzer
        from rich.table import Table
        
        engine = BurrowEngine(project_root=args.project_root, llm_provider=args.llm_provider)
        console.print("[bold cyan]Initializing codebase AST & Symbol Graph analysis...[/]")
        builder = SymbolGraphBuilder(engine.project_root)
        builder.build()
        analyzer = SymbolGraphAnalyzer(engine.project_root, builder)
        smells = analyzer.analyze()
        
        if not smells:
            console.print("[bold green]✔ No codebase vulnerabilities or smells detected![/]")
            sys.exit(0)
            
        table = Table(title="[bold yellow]CODEBASE VULNERABILITIES & SMELLS[/]", box=box.ROUNDED)
        table.add_column("File Path", style="cyan")
        table.add_column("Line", style="magenta", justify="right")
        table.add_column("Type", style="blue")
        table.add_column("Severity", justify="center")
        table.add_column("Description", style="white")
        
        has_error = False
        for smell in smells:
            sev = smell.severity.lower()
            if sev == "error":
                sev_str = "[bold red]ERROR[/]"
                has_error = True
            elif sev == "warning":
                sev_str = "[bold yellow]WARNING[/]"
            else:
                sev_str = "[bold cyan]INFO[/]"
                
            table.add_row(
                smell.file_path,
                str(smell.line_number),
                smell.smell_type,
                sev_str,
                smell.message
            )
            
        console.print(table)
        if has_error:
            console.print("\n[bold red]✖ High-severity codebase errors detected. Check failed.[/]")
            sys.exit(1)
        else:
            console.print("\n[bold yellow]✔ Codebase scan completed with warnings/info.[/]")
            sys.exit(0)
    except Exception as e:
        error_console.print(f"[bold red]Error:[/] {e}")
        sys.exit(1)


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

    # check subcommand
    check_parser = subparsers.add_parser("check", help="Scan the codebase statically and list code smells")
    check_parser.set_defaults(func=handle_check)

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
