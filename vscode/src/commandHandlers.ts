import * as vscode from 'vscode';
import { BackendConnector } from './backendConnector';
import { DiagnosticsBridge } from './diagnosticsBridge';

export class CommandHandlers {
    private backendConnector: BackendConnector;
    private diagnosticsBridge: DiagnosticsBridge;

    constructor(backendConnector: BackendConnector, diagnosticsBridge: DiagnosticsBridge) {
        this.backendConnector = backendConnector;
        this.diagnosticsBridge = diagnosticsBridge;
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
}
