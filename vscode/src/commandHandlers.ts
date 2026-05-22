import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { BackendConnector } from './backendConnector';
import { DiagnosticsBridge } from './diagnosticsBridge';
import { StateManager, SharedState } from './stateManager';
import { PatchProvider } from './patchProvider';

interface SuggestionQuickPickItem extends vscode.QuickPickItem {
    suggestion: any;
    index: number;
}

export class CommandHandlers {
    private backendConnector: BackendConnector;
    private diagnosticsBridge: DiagnosticsBridge;
    private stateManager: StateManager;
    private patchProvider: PatchProvider;

    constructor(
        backendConnector: BackendConnector,
        diagnosticsBridge: DiagnosticsBridge,
        stateManager: StateManager
    ) {
        this.backendConnector = backendConnector;
        this.diagnosticsBridge = diagnosticsBridge;
        this.stateManager = stateManager;
        this.patchProvider = new PatchProvider();
    }

    /**
     * Analyzes selected text in the active text editor.
     */
    public async analyzeSelection(): Promise<void> {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showErrorMessage('Burrow: No active text editor open.');
            return;
        }

        const selection = editor.selection;
        const selectedText = editor.document.getText(selection).trim();
        if (!selectedText) {
            vscode.window.showWarningMessage('Burrow: Please select some traceback error content to analyze.');
            return;
        }

        await vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: 'Burrow: Analyzing selection...',
            cancellable: false
        }, async () => {
            try {
                const result = await this.backendConnector.analyze(selectedText);
                if (result) {
                    this.diagnosticsBridge.updateDiagnostics(result);
                    vscode.window.showInformationMessage('Burrow: Analysis complete. Diagnostics updated in editor.');
                }
            } catch (err: any) {
                vscode.window.showErrorMessage(`Burrow: Analysis failed: ${err.message}`);
            }
        });
    }

    /**
     * Analyzes the entire content of the active text editor.
     */
    public async analyzeFile(): Promise<void> {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showErrorMessage('Burrow: No active text editor open.');
            return;
        }

        const fileContent = editor.document.getText().trim();
        if (!fileContent) {
            vscode.window.showWarningMessage('Burrow: Active file is empty.');
            return;
        }

        await vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: 'Burrow: Analyzing active file...',
            cancellable: false
        }, async () => {
            try {
                const result = await this.backendConnector.analyze(fileContent);
                if (result) {
                    this.diagnosticsBridge.updateDiagnostics(result);
                    vscode.window.showInformationMessage('Burrow: Analysis complete. Diagnostics updated in editor.');
                }
            } catch (err: any) {
                vscode.window.showErrorMessage(`Burrow: Analysis failed: ${err.message}`);
            }
        });
    }

    /**
     * Previews a remediation suggestion in a side-by-side diff.
     */
    public async previewPatch(suggestion?: any, index?: number): Promise<void> {
        const result = SharedState.lastAnalysisResult;
        if (!result || !result.remediation_result || !result.remediation_result.suggestions) {
            vscode.window.showWarningMessage('Burrow: No remediation suggestions available to preview. Run analysis first.');
            return;
        }

        let targetSuggestion = suggestion;
        let targetIndex = index;

        // If command is run from command palette without arguments, show QuickPick
        if (!targetSuggestion || targetIndex === undefined) {
            const items = result.remediation_result.suggestions.map((s: any, idx: number) => ({
                label: s.description,
                description: `${path.basename(s.affected_file)} [${s.risk_level.toUpperCase()}]`,
                detail: s.rationale,
                suggestion: s,
                index: idx
            }));

            if (items.length === 0) {
                vscode.window.showInformationMessage('Burrow: No suggestions found to preview.');
                return;
            }

            const selection = await vscode.window.showQuickPick<SuggestionQuickPickItem>(items, {
                placeHolder: 'Select a remediation patch to preview'
            });

            if (!selection) {
                return;
            }

            targetSuggestion = selection.suggestion;
            targetIndex = selection.index;
        }

        try {
            const workspaceRoot = this.stateManager.workspaceRoot;
            const resolvedPath = this.resolvePath(targetSuggestion.affected_file, workspaceRoot);
            
            const originalUri = vscode.Uri.file(resolvedPath);
            const patchUri = vscode.Uri.parse(
                `${PatchProvider.scheme}://preview?filePath=${encodeURIComponent(resolvedPath)}&suggestionIndex=${targetIndex}`
            );

            await vscode.commands.executeCommand(
                'vscode.diff',
                originalUri,
                patchUri,
                `Burrow Fix: ${targetSuggestion.description}`
            );
        } catch (err: any) {
            vscode.window.showErrorMessage(`Burrow: Failed to open patch preview: ${err.message}`);
        }
    }

    /**
     * Applies a suggested fix directly to the target file.
     */
    public async applyPatch(suggestion?: any, index?: number): Promise<void> {
        const result = SharedState.lastAnalysisResult;
        if (!result || !result.remediation_result || !result.remediation_result.suggestions) {
            vscode.window.showWarningMessage('Burrow: No suggestions available to apply.');
            return;
        }

        let targetSuggestion = suggestion;
        let targetIndex = index;

        if (!targetSuggestion || targetIndex === undefined) {
            const items = result.remediation_result.suggestions.map((s: any, idx: number) => ({
                label: s.description,
                description: `${path.basename(s.affected_file)} [${s.risk_level.toUpperCase()}]`,
                detail: s.rationale,
                suggestion: s,
                index: idx
            }));

            if (items.length === 0) {
                vscode.window.showInformationMessage('Burrow: No suggestions found.');
                return;
            }

            const selection = await vscode.window.showQuickPick<SuggestionQuickPickItem>(items, {
                placeHolder: 'Select a remediation patch to apply'
            });

            if (!selection) {
                return;
            }

            targetSuggestion = selection.suggestion;
            targetIndex = selection.index;
        }

        try {
            const workspaceRoot = this.stateManager.workspaceRoot;
            if (!workspaceRoot) {
                vscode.window.showErrorMessage('Burrow: Workspace root is undefined.');
                return;
            }

            const resolvedPath = this.resolvePath(targetSuggestion.affected_file, workspaceRoot);
            
            // 1. Trust boundary: path traversal check
            const relativePath = path.relative(workspaceRoot, resolvedPath);
            if (relativePath.startsWith('..') || path.isAbsolute(relativePath)) {
                vscode.window.showErrorMessage(`Burrow Security Violation: Target path "${targetSuggestion.affected_file}" lies outside the workspace root.`);
                return;
            }

            // 2. Trust boundary: allowed write paths check
            const allowed = this.stateManager.allowedWritePaths;
            if (allowed) {
                const allowedList = allowed.split(',').map(p => p.trim()).filter(Boolean);
                if (allowedList.length > 0) {
                    let allowedMatch = false;
                    for (const allowedRel of allowedList) {
                        const allowedAbs = path.resolve(workspaceRoot, allowedRel);
                        const relToAllowed = path.relative(allowedAbs, resolvedPath);
                        if (!relToAllowed.startsWith('..') && !path.isAbsolute(relToAllowed)) {
                            allowedMatch = true;
                            break;
                        }
                    }
                    if (!allowedMatch) {
                        vscode.window.showErrorMessage(`Burrow Security Violation: File "${targetSuggestion.affected_file}" is not in the allowed write scope list: ${allowed}`);
                        return;
                    }
                }
            }

            // 3. Check minimum confidence threshold
            const confidence = targetSuggestion.confidence_score !== undefined ? targetSuggestion.confidence_score : 0.50;
            const minConf = this.stateManager.patchMinConfidence;
            if (confidence < minConf) {
                vscode.window.showErrorMessage(`Burrow: Cannot apply patch. Confidence score (${(confidence * 100).toFixed(0)}%) is below configured minimum (${(minConf * 100).toFixed(0)}%).`);
                return;
            }

            // 4. Modal confirmation (never silently edit)
            const choice = await vscode.window.showInformationMessage(
                `Apply patch: "${targetSuggestion.description}" to file "${targetSuggestion.affected_file}"?`,
                { modal: true },
                'Apply Patch'
            );
            if (choice !== 'Apply Patch') {
                return;
            }

            const fileUri = vscode.Uri.file(resolvedPath);

            let originalContent = '';
            if (fs.existsSync(resolvedPath)) {
                try {
                    const doc = vscode.workspace.textDocuments.find(d => d.uri.fsPath === resolvedPath);
                    if (doc) {
                        originalContent = doc.getText();
                    } else {
                        originalContent = await fs.promises.readFile(resolvedPath, 'utf8');
                    }
                } catch {
                    originalContent = await fs.promises.readFile(resolvedPath, 'utf8');
                }
            }

            const patchedContent = this.patchProvider.applyPatch(originalContent, targetSuggestion);

            const workspaceEdit = new vscode.WorkspaceEdit();
            if (!fs.existsSync(resolvedPath)) {
                workspaceEdit.createFile(fileUri, { overwrite: true, ignoreIfExists: false });
                workspaceEdit.insert(fileUri, new vscode.Position(0, 0), patchedContent);
            } else {
                const document = await vscode.workspace.openTextDocument(fileUri);
                const fullRange = new vscode.Range(
                    document.positionAt(0),
                    document.positionAt(document.getText().length)
                );
                workspaceEdit.replace(fileUri, fullRange, patchedContent);
            }

            const success = await vscode.workspace.applyEdit(workspaceEdit);
            if (success) {
                vscode.window.showInformationMessage(`Burrow: Applied fix: "${targetSuggestion.description}"`);
            } else {
                vscode.window.showErrorMessage('Burrow: Failed to apply edit in workspace.');
            }
        } catch (err: any) {
            vscode.window.showErrorMessage(`Burrow: Failed to apply patch: ${err.message}`);
        }
    }

    /**
     * Explains the last analysis in a native side-by-side Markdown document preview.
     */
    public async explainMore(): Promise<void> {
        const result = SharedState.lastAnalysisResult;
        if (!result) {
            vscode.window.showInformationMessage('Burrow: No analysis results available yet.');
            return;
        }

        const error = result.error;
        const rca = result.rca_result;
        const remediation = result.remediation_result;
        const recommend = result.recommendation;

        let md = `# 🕳️ Burrow Debugging Report\n\n`;
        md += `### 🔍 Analysis Overview\n`;
        md += `*   **Language**: \`${error.language.toUpperCase()}\`\n`;
        md += `*   **Error Type**: \`${error.error_type}\`\n`;
        md += `*   **Error Message**: \`${error.message}\`\n`;
        md += `*   **Confidence**: \`${(error.confidence_score * 100).toFixed(0)}%\`\n\n`;

        if (rca && rca.hypotheses && rca.hypotheses.length > 0) {
            md += `### 🧠 Inferred Root Causes & Hypotheses\n`;
            for (const hyp of rca.hypotheses) {
                md += `#### 🔴 **${hyp.type.toUpperCase()}**: ${hyp.root_cause}\n`;
                md += `*   **Reasoning**: ${hyp.reasoning_summary}\n`;
                md += `*   **Safest Fix Direction**: ${hyp.safest_fix_direction}\n`;
                md += `*   **Origin File**: \`${hyp.origin_file}:${hyp.line_number || 1}\`\n`;
                md += `*   **Confidence**: \`${(hyp.confidence_score * 100).toFixed(0)}%\`\n\n`;
            }
        }

        if (recommend) {
            md += `### 💡 AI Diagnosis & Recommendation\n`;
            md += `*   **Cause**: ${recommend.cause}\n`;
            md += `*   **Remedy Recommendation**:\n${recommend.remediation}\n\n`;
        }

        if (remediation && remediation.suggestions && remediation.suggestions.length > 0) {
            md += `### 🛠️ Suggested Remediation Steps\n`;
            for (let i = 0; i < remediation.suggestions.length; i++) {
                const s = remediation.suggestions[i];
                md += `#### ${i + 1}. [${s.risk_level.toUpperCase()}] ${s.description}\n`;
                md += `*   **Target File**: \`${s.affected_file}\`\n`;
                md += `*   **Rationale**: ${s.rationale}\n`;
                if (s.patch_preview) {
                    md += `*   **Code Preview**:\n\`\`\`${error.language}\n${s.patch_preview.replace(/^```(?:\w+)?\n/, '').replace(/\n```$/, '')}\n\`\`\`\n`;
                }
                md += `\n`;
            }
        }

        try {
            const doc = await vscode.workspace.openTextDocument({
                content: md,
                language: 'markdown'
            });
            await vscode.window.showTextDocument(doc, vscode.ViewColumn.Beside);
        } catch (err: any) {
            vscode.window.showErrorMessage(`Burrow: Failed to open explanation document: ${err.message}`);
        }
    }

    /**
     * Opens a file and highlights/focuses a specific line.
     */
    public async openFile(filePath: string, lineNumber: number): Promise<void> {
        try {
            const workspaceRoot = this.stateManager.workspaceRoot;
            const resolvedPath = this.resolvePath(filePath, workspaceRoot);

            if (!fs.existsSync(resolvedPath)) {
                vscode.window.showErrorMessage(`Burrow: File not found at path: ${resolvedPath}`);
                return;
            }

            const doc = await vscode.workspace.openTextDocument(resolvedPath);
            const editor = await vscode.window.showTextDocument(doc);
            const line = Math.max(0, lineNumber - 1);
            const pos = new vscode.Position(line, 0);
            
            editor.selection = new vscode.Selection(pos, pos);
            editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
        } catch (err: any) {
            vscode.window.showErrorMessage(`Burrow: Failed to open file: ${err.message}`);
        }
    }

    /**
     * Manually start the backend server.
     */
    public async startBackend(): Promise<void> {
        vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: 'Burrow: Starting API service...',
            cancellable: false
        }, async () => {
            await this.backendConnector.startBackend();
        });
    }

    /**
     * Manually stop the backend server.
     */
    public stopBackend(): void {
        this.backendConnector.stopBackend();
    }

    /**
     * Clear all active visual diagnostics.
     */
    public clearDiagnostics(): void {
        this.diagnosticsBridge.clear();
        vscode.window.showInformationMessage('Burrow: Cleared active diagnostics.');
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
}
