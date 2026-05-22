import * as vscode from 'vscode';
import * as path from 'path';
import { SharedState } from './stateManager';

export class BurrowHoverProvider implements vscode.HoverProvider {
    public provideHover(
        document: vscode.TextDocument,
        position: vscode.Position,
        token: vscode.CancellationToken
    ): vscode.ProviderResult<vscode.Hover> {
        const result = SharedState.lastAnalysisResult;
        if (!result) {
            return null;
        }

        // Get diagnostics on this line
        const diagnostics = vscode.languages.getDiagnostics(document.uri);
        const lineDiagnostics = diagnostics.filter(diag => {
            return diag.source && 
                   diag.source.startsWith('Burrow') && 
                   diag.range.start.line <= position.line && 
                   diag.range.end.line >= position.line;
        });

        if (lineDiagnostics.length === 0) {
            return null;
        }

        const markdown = new vscode.MarkdownString();
        markdown.isTrusted = true;
        markdown.supportHtml = true;

        markdown.appendMarkdown('### 🕳️ Burrow Debugging Insights\n\n');

        // Check if there's a Root Cause diagnostic here
        const rcaDiag = lineDiagnostics.find(d => d.code === 'root_cause');
        if (rcaDiag) {
            markdown.appendMarkdown('#### 🔍 Detected Root Cause\n');
            markdown.appendMarkdown(`${rcaDiag.message}\n\n`);
        }

        // Check for traceback frame info
        const tbDiag = lineDiagnostics.find(d => d.code === 'traceback_frame');
        if (tbDiag) {
            markdown.appendMarkdown('#### ⚠️ Traceback Crash Frame\n');
            markdown.appendMarkdown(`${tbDiag.message}\n\n`);
        }

        // Append matching remediation suggestions for this file
        if (result.remediation_result && result.remediation_result.suggestions) {
            const currentFileLower = document.uri.fsPath.toLowerCase();
            const matchingSuggestions = result.remediation_result.suggestions
                .map((s: any, idx: number) => ({ s, idx }))
                .filter((item: { s: any; idx: number }) => {
                    const s = item.s;
                    const affectedLower = s.affected_file.toLowerCase();
                    return currentFileLower === affectedLower ||
                           currentFileLower.endsWith(affectedLower.replace(/\\/g, '/')) ||
                           affectedLower.endsWith(currentFileLower.replace(/\\/g, '/'));
                });

            if (matchingSuggestions.length > 0) {
                markdown.appendMarkdown('---\n\n#### 💡 Suggested Remedies\n');
                for (const { s, idx } of matchingSuggestions) {
                    const argStr = encodeURIComponent(JSON.stringify([s, idx]));
                    const previewLink = `[Preview Patch](command:burrow.previewPatch?${argStr})`;
                    const applyLink = `[Apply Patch](command:burrow.applyPatch?${argStr})`;

                    markdown.appendMarkdown(`*   **${s.description}**  
    *Risk level: \`${s.risk_level.toUpperCase()}\`*  
    *Rationale: ${s.rationale}*  
    ⚡ ${previewLink} | ${applyLink}\n\n`);
                }
            }
        }

        // Add code smell details if present
        const smellDiag = lineDiagnostics.find(d => d.code !== 'root_cause' && d.code !== 'traceback_frame' && d.code !== 'remediation');
        if (smellDiag && !rcaDiag && !tbDiag) {
            markdown.appendMarkdown('#### 🔎 AST Code Smell\n');
            markdown.appendMarkdown(`${smellDiag.message}\n\n`);
        }

        return new vscode.Hover(markdown);
    }
}
