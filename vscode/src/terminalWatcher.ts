import * as vscode from 'vscode';
import { BackendConnector } from './backendConnector';
import { DiagnosticsBridge } from './diagnosticsBridge';

export class TerminalWatcher {
    private backendConnector: BackendConnector;
    private diagnosticsBridge: DiagnosticsBridge;
    private disposable: vscode.Disposable | null = null;

    constructor(backendConnector: BackendConnector, diagnosticsBridge: DiagnosticsBridge) {
        this.backendConnector = backendConnector;
        this.diagnosticsBridge = diagnosticsBridge;
    }

    /**
     * Start listening to terminal executions.
     */
    public start(): void {
        if (this.disposable) {
            return;
        }

        // Subscribe to the stable onDidEndTerminalShellExecution API
        this.disposable = vscode.window.onDidEndTerminalShellExecution(async (event) => {
            // Only trigger if exitCode indicates failure (non-zero) or is undefined (crashed/unknown)
            if (event.exitCode === 0) {
                return;
            }

            try {
                // Collect output stream chunks
                const chunks: string[] = [];
                for await (const chunk of event.execution.read()) {
                    chunks.push(chunk);
                }
                const rawOutput = chunks.join('');
                const cleanOutput = this.stripAnsi(rawOutput);

                if (this.hasErrorSignature(cleanOutput)) {
                    await vscode.window.withProgress({
                        location: vscode.ProgressLocation.Notification,
                        title: 'Burrow: Analyzing terminal traceback...',
                        cancellable: false
                    }, async () => {
                        const result = await this.backendConnector.analyze(cleanOutput);
                        if (result) {
                            this.diagnosticsBridge.updateDiagnostics(result);
                            
                            // Highlight root cause if found
                            if (result.rca_result && result.rca_result.hypotheses && result.rca_result.hypotheses.length > 0) {
                                const mainHypothesis = result.rca_result.hypotheses[0];
                                vscode.window.showWarningMessage(
                                    `Burrow: Detected failure. Probable root cause: ${mainHypothesis.root_cause}`,
                                    'View Diagnostics'
                                ).then(selection => {
                                    if (selection === 'View Diagnostics') {
                                        vscode.commands.executeCommand('workbench.action.showErrorsWarnings');
                                    }
                                });
                            } else {
                                vscode.window.showInformationMessage('Burrow: Terminal failure analyzed. No clear root cause determined.');
                            }
                        }
                    });
                }
            } catch (err: any) {
                console.error('Burrow TerminalWatcher error:', err);
            }
        });
    }

    /**
     * Stop listening to terminal executions.
     */
    public stop(): void {
        if (this.disposable) {
            this.disposable.dispose();
            this.disposable = null;
        }
    }

    /**
     * Strips ANSI escape codes from terminal output.
     */
    private stripAnsi(text: string): string {
        const pattern = [
            '[\\u001B\\u009B][[\\]()#;?]*(?:(?:(?:[a-zA-Z\\d]*(?:;[-a-zA-Z\\d\\/#&.:=?%@~_]*)*)?\\u0007)',
            '(?:(?:\\d{1,4}(?:;\\d{0,4})*)?[\\dA-PR-TZcf-ntqry=><~]))'
        ].join('|');
        const regex = new RegExp(pattern, 'g');
        return text.replace(regex, '');
    }

    /**
     * Scans text for traceback error signatures.
     */
    private hasErrorSignature(text: string): boolean {
        return text.includes('Traceback (most recent call last):') ||
            text.includes('Exception in ') ||
            /at \s*[\w.<>]+.*:\d+:\d+/.test(text) ||
            /[a-zA-Z0-9_\-\./\\+@ ]+:\d+(:\d+)?: error/.test(text) ||
            /TypeError:/.test(text) ||
            /ReferenceError:/.test(text) ||
            /SyntaxError:/.test(text);
    }

    public dispose(): void {
        this.stop();
    }
}
