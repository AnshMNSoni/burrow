import * as vscode from 'vscode';
import * as path from 'path';
import { SharedState } from './stateManager';

function isSameFile(pathA: string, pathB: string): boolean {
    if (!pathA || !pathB) {
        return false;
    }
    const a = path.resolve(pathA).toLowerCase().replace(/\\/g, '/');
    const b = path.resolve(pathB).toLowerCase().replace(/\\/g, '/');
    return a === b || a.endsWith('/' + b) || b.endsWith('/' + a);
}

function getMarkdownLanguage(filePath: string): string {
    const ext = path.extname(filePath).toLowerCase();
    switch (ext) {
        case '.py': return 'python';
        case '.js':
        case '.jsx': return 'javascript';
        case '.ts':
        case '.tsx': return 'typescript';
        case '.go': return 'go';
        case '.java': return 'java';
        case '.cpp':
        case '.cc':
        case '.h': return 'cpp';
        case '.cs': return 'csharp';
        case '.json': return 'json';
        case '.html': return 'html';
        case '.css': return 'css';
        default: return '';
    }
}

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

        // Find diagnostic categories on this line
        const rcaDiag = lineDiagnostics.find(d => d.code === 'root_cause');
        const tbDiag = lineDiagnostics.find(d => d.code === 'traceback_frame');
        const smellDiag = lineDiagnostics.find(d => d.code !== 'root_cause' && d.code !== 'traceback_frame' && d.code !== 'remediation');

        const markdown = new vscode.MarkdownString();
        markdown.isTrusted = true;
        markdown.supportHtml = true;

        markdown.appendMarkdown('### 🕳️ Burrow Insights\n\n');

        // 1. Root Cause Analysis
        if (rcaDiag) {
            let matchingHypotheses = result.rca_result?.hypotheses?.filter((hyp: any) => {
                return isSameFile(hyp.origin_file, document.uri.fsPath) && 
                       ((hyp.line_number || 1) === position.line + 1);
            }) || [];
            
            if (matchingHypotheses.length === 0) {
                // Fallback to match by substring of root cause
                matchingHypotheses = result.rca_result?.hypotheses?.filter((hyp: any) => {
                    return isSameFile(hyp.origin_file, document.uri.fsPath) &&
                           rcaDiag.message.toLowerCase().includes(hyp.root_cause.toLowerCase());
                }) || [];
            }

            if (matchingHypotheses.length > 0) {
                markdown.appendMarkdown('#### 🔍 Root Cause Analysis\n');
                for (const hyp of matchingHypotheses) {
                    markdown.appendMarkdown(`> **Type:** \`${hyp.type.toUpperCase()}\`  \n`);
                    markdown.appendMarkdown(`> **Confidence:** \`${(hyp.confidence_score * 100).toFixed(0)}%\`  \n`);
                    markdown.appendMarkdown(`> **Root Cause:** ${hyp.root_cause}  \n`);
                    markdown.appendMarkdown(`> \n`);
                    markdown.appendMarkdown(`> **Reasoning:** ${hyp.reasoning_summary}  \n`);
                    markdown.appendMarkdown(`> **Safest Fix:** ${hyp.safest_fix_direction}\n\n`);
                }
            } else {
                // Raw diagnostic message fallback
                markdown.appendMarkdown('#### 🔍 Root Cause Analysis\n');
                markdown.appendMarkdown(`> ${rcaDiag.message}\n\n`);
            }
            markdown.appendMarkdown('---\n\n');
        }

        // 2. Traceback Frames
        if (tbDiag) {
            const allFrames: { frame: any; errType: string; errMsg: string }[] = [];
            const collectFrames = (err: any) => {
                if (err.frames) {
                    for (const f of err.frames) {
                        allFrames.push({ frame: f, errType: err.error_type, errMsg: err.message });
                    }
                }
                if (err.chained_errors) {
                    for (const chained of err.chained_errors) {
                        collectFrames(chained);
                    }
                }
            };
            if (result.error) {
                collectFrames(result.error);
            }

            const matchingFrames = allFrames.filter((item: any) => {
                return isSameFile(item.frame.file_path, document.uri.fsPath) &&
                       ((item.frame.line_number || 1) === position.line + 1);
            });

            if (matchingFrames.length > 0) {
                markdown.appendMarkdown('#### ⚠️ Traceback Crash Frame\n');
                for (const item of matchingFrames) {
                    const f = item.frame;
                    const lang = getMarkdownLanguage(document.uri.fsPath);
                    markdown.appendMarkdown(`\`${f.function_name || 'anonymous'}()\` at line ${f.line_number || 1} in \`${path.basename(f.file_path)}\`  \n`);
                    if (f.raw_line) {
                        markdown.appendMarkdown(`\`\`\`${lang}\n${f.raw_line.trim()}\n\`\`\`\n`);
                    }
                }
            } else {
                // Raw fallback
                markdown.appendMarkdown('#### ⚠️ Traceback Crash Frame\n');
                markdown.appendMarkdown(`> ${tbDiag.message}\n\n`);
            }
            markdown.appendMarkdown('---\n\n');
        }

        // 3. Code Smells
        if (smellDiag && !rcaDiag && !tbDiag) {
            const matchingSmells = result.symbol_graph_data?.smells?.filter((smell: any) => {
                return isSameFile(smell.file_path, document.uri.fsPath) &&
                       (smell.line_number === position.line + 1);
            }) || [];

            if (matchingSmells.length > 0) {
                markdown.appendMarkdown('#### 🔎 Code Smells\n');
                for (const smell of matchingSmells) {
                    markdown.appendMarkdown(`*   **Type:** \`${smell.smell_type}\` (Severity: \`${smell.severity.toUpperCase()}\`)  \n`);
                    markdown.appendMarkdown(`    *Message:* ${smell.message}\n\n`);
                }
            } else {
                // Raw fallback
                markdown.appendMarkdown('#### 🔎 Code Smell\n');
                markdown.appendMarkdown(`> ${smellDiag.message}\n\n`);
            }
            markdown.appendMarkdown('---\n\n');
        }

        // 4. Remediation Suggestions for this file
        if (result.remediation_result && result.remediation_result.suggestions) {
            const matchingSuggestions = result.remediation_result.suggestions
                .map((s: any, idx: number) => ({ s, idx }))
                .filter((item: { s: any; idx: number }) => {
                    return isSameFile(item.s.affected_file, document.uri.fsPath);
                });

            if (matchingSuggestions.length > 0) {
                markdown.appendMarkdown('#### 💡 Suggested Remedies\n');
                for (const { s, idx } of matchingSuggestions) {
                    const argStr = encodeURIComponent(JSON.stringify([s, idx]));
                    const previewLink = `[Preview Patch](command:burrow.previewPatch?${argStr})`;
                    const applyLink = `[Apply Patch](command:burrow.applyPatch?${argStr})`;

                    markdown.appendMarkdown(`*   **${s.description}**  \n`);
                    markdown.appendMarkdown(`    *Risk Level: \`${s.risk_level.toUpperCase()}\`*  \n`);
                    markdown.appendMarkdown(`    *Rationale: ${s.rationale}*  \n`);
                    markdown.appendMarkdown(`    ⚡ ${previewLink} | ${applyLink}\n\n`);
                }
            }
        }

        return new vscode.Hover(markdown);
    }
}
