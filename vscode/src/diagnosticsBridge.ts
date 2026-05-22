import * as vscode from 'vscode';
import * as path from 'path';
import { StateManager, SharedState } from './stateManager';
import { BurrowTreeView } from './treeView';

// Define typed interfaces matching Pydantic backend models
export interface NormalizedFrame {
    file_path: string;
    line_number?: number;
    column_number?: number;
    function_name?: string;
    module_name?: string;
    code_context?: string;
    raw_line: string;
    is_application_code: boolean;
    metadata?: Record<string, any>;
}

export interface NormalizedError {
    error_type: string;
    message: string;
    frames: NormalizedFrame[];
    language: string;
    raw_input: string;
    root_origin?: NormalizedFrame;
    surfaced_crash_point?: NormalizedFrame;
    confidence_score: number;
    chained_errors?: NormalizedError[];
    metadata?: Record<string, any>;
}

export interface Hypothesis {
    type: string;
    root_cause: string;
    origin_file: string;
    line_number?: number;
    probable_impacted_modules: string[];
    reasoning_summary: string;
    safest_fix_direction: string;
    confidence_score: number;
}

export interface RCAResult {
    hypotheses: Hypothesis[];
    propagation_chain: string[];
}

export interface FixSuggestion {
    description: string;
    affected_file: string;
    likely_edit_region?: string;
    rationale: string;
    risk_level: string;
    patch_preview?: string;
}

export interface RemediationResult {
    suggestions: FixSuggestion[];
}

export interface CodeSmell {
    smell_type: string;
    message: string;
    file_path: string;
    line_number: number;
    severity: string;
}

export interface SymbolGraphData {
    nodes: any[];
    edges: any[];
    smells: CodeSmell[];
}

export interface AnalysisResult {
    error: NormalizedError;
    recommendation?: {
        recommendation: string;
        explanation?: string;
    };
    graph?: any;
    workspace_context?: any;
    symbol_graph_data?: SymbolGraphData;
    rca_result?: RCAResult;
    remediation_result?: RemediationResult;
}

export class DiagnosticsBridge {
    private diagnosticCollection: vscode.DiagnosticCollection;
    private stateManager: StateManager;
    private treeView?: BurrowTreeView;

    constructor(stateManager: StateManager) {
        this.stateManager = stateManager;
        this.diagnosticCollection = vscode.languages.createDiagnosticCollection('burrow');
    }

    public setTreeView(treeView: BurrowTreeView): void {
        this.treeView = treeView;
    }

    /**
     * Clears all diagnostic squigglies.
     */
    public clear(): void {
        this.diagnosticCollection.clear();
    }

    /**
     * Map analysis results to VSCode diagnostics collection.
     */
    public updateDiagnostics(result: AnalysisResult): void {
        this.clear();
        SharedState.lastAnalysisResult = result;
        if (this.treeView) {
            this.treeView.refresh();
        }

        const workspaceRoot = this.stateManager.workspaceRoot;
        const fileDiagnosticsMap = new Map<string, vscode.Diagnostic[]>();

        const getDiagnosticsForFile = (filePath: string): vscode.Diagnostic[] => {
            const resolvedPath = this.resolvePath(filePath, workspaceRoot);
            if (!fileDiagnosticsMap.has(resolvedPath)) {
                fileDiagnosticsMap.set(resolvedPath, []);
            }
            return fileDiagnosticsMap.get(resolvedPath)!;
        };

        // 1. Process Traceback frames recursively
        if (result.error) {
            this.processErrorFrames(result.error, result.error.error_type, result.error.message, getDiagnosticsForFile);
        }

        // 2. Process RCA hypotheses
        if (result.rca_result && result.rca_result.hypotheses) {
            for (const hypothesis of result.rca_result.hypotheses) {
                if (!hypothesis.origin_file) {
                    continue;
                }

                const line = (hypothesis.line_number && hypothesis.line_number > 0) ? hypothesis.line_number - 1 : 0;
                const range = new vscode.Range(line, 0, line, 100);

                const msg = `[Burrow Root Cause] ${hypothesis.type.toUpperCase()}: ${hypothesis.root_cause} (Confidence: ${(hypothesis.confidence_score * 100).toFixed(0)}%)`;


                const diagnostic = new vscode.Diagnostic(
                    range,
                    msg,
                    vscode.DiagnosticSeverity.Error
                );
                diagnostic.source = 'Burrow RCA';
                diagnostic.code = 'root_cause';

                getDiagnosticsForFile(hypothesis.origin_file).push(diagnostic);
            }
        }

        // 3. Process Fix suggestions
        if (result.remediation_result && result.remediation_result.suggestions) {
            for (const suggestion of result.remediation_result.suggestions) {
                if (!suggestion.affected_file) {
                    continue;
                }

                const line = 0; // Top of file by default
                const range = new vscode.Range(line, 0, line, 100);

                const msg = `[Burrow Fix Suggestion] ${suggestion.description} (${suggestion.risk_level.toUpperCase()})`;


                const diagnostic = new vscode.Diagnostic(
                    range,
                    msg,
                    vscode.DiagnosticSeverity.Information
                );
                diagnostic.source = 'Burrow Remediation';
                diagnostic.code = 'remediation';

                getDiagnosticsForFile(suggestion.affected_file).push(diagnostic);
            }
        }

        // 4. Process Code Smells from symbol graph
        if (result.symbol_graph_data && result.symbol_graph_data.smells) {
            for (const smell of result.symbol_graph_data.smells) {
                if (!smell.file_path) {
                    continue;
                }

                const line = (smell.line_number && smell.line_number > 0) ? smell.line_number - 1 : 0;
                const range = new vscode.Range(line, 0, line, 100);

                const severity = this.mapSeverity(smell.severity);
                const msg = `[Burrow Code Smell] ${smell.smell_type}: ${smell.message}`;

                const diagnostic = new vscode.Diagnostic(
                    range,
                    msg,
                    severity
                );
                diagnostic.source = 'Burrow Symbol Analyzer';
                diagnostic.code = smell.smell_type;

                getDiagnosticsForFile(smell.file_path).push(diagnostic);
            }
        }

        // Set diagnostics into VSCode collections
        for (const [filePath, diagnostics] of fileDiagnosticsMap.entries()) {
            try {
                const uri = vscode.Uri.file(filePath);
                this.diagnosticCollection.set(uri, diagnostics);
            } catch (err) {
                // Ignore invalid file paths
            }
        }
    }

    private processErrorFrames(
        error: NormalizedError,
        errType: string,
        errMsg: string,
        getDiagnostics: (path: string) => vscode.Diagnostic[]
    ): void {
        if (error.frames) {
            for (const frame of error.frames) {
                if (!frame.file_path) {
                    continue;
                }

                const line = (frame.line_number && frame.line_number > 0) ? frame.line_number - 1 : 0;
                const col = (frame.column_number && frame.column_number > 0) ? frame.column_number - 1 : 0;
                const range = new vscode.Range(line, col, line, col + 50);

                const severity = frame.is_application_code
                    ? vscode.DiagnosticSeverity.Error
                    : vscode.DiagnosticSeverity.Warning;

                const msg = `[Burrow Traceback] ${errType}: ${errMsg} in ${frame.function_name || 'unknown'}()`;


                const diagnostic = new vscode.Diagnostic(
                    range,
                    msg,
                    severity
                );
                diagnostic.source = 'Burrow Parser';
                diagnostic.code = 'traceback_frame';

                getDiagnostics(frame.file_path).push(diagnostic);
            }
        }

        if (error.chained_errors) {
            for (const chained of error.chained_errors) {
                this.processErrorFrames(chained, chained.error_type, chained.message, getDiagnostics);
            }
        }
    }

    private resolvePath(filePath: string, workspaceRoot?: string): string {
        if (path.isAbsolute(filePath)) {
            return filePath;
        }
        if (workspaceRoot) {
            return path.resolve(workspaceRoot, filePath);
        }
        return filePath;
    }

    private mapSeverity(severity: string): vscode.DiagnosticSeverity {
        switch (severity.toLowerCase()) {
            case 'error':
                return vscode.DiagnosticSeverity.Error;
            case 'warning':
                return vscode.DiagnosticSeverity.Warning;
            case 'info':
            case 'information':
                return vscode.DiagnosticSeverity.Information;
            default:
                return vscode.DiagnosticSeverity.Warning;
        }
    }

    public dispose(): void {
        this.diagnosticCollection.dispose();
    }
}
