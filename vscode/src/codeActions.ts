import * as vscode from 'vscode';
import { SharedState } from './stateManager';

export class BurrowCodeActionsProvider implements vscode.CodeActionProvider {
    public static readonly providedCodeActionKinds = [
        vscode.CodeActionKind.QuickFix
    ];

    public provideCodeActions(
        document: vscode.TextDocument,
        range: vscode.Range | vscode.Selection,
        context: vscode.CodeActionContext,
        token: vscode.CancellationToken
    ): vscode.ProviderResult<vscode.CodeAction[]> {
        const hasBurrowDiagnostics = context.diagnostics.some(
            diag => diag.source && diag.source.startsWith('Burrow')
        );

        if (!hasBurrowDiagnostics) {
            return [];
        }

        const actions: vscode.CodeAction[] = [];

        // 1. General explanation quick fix
        const explainAction = new vscode.CodeAction('Burrow: Explain In-Editor Diagnostics', vscode.CodeActionKind.QuickFix);
        explainAction.command = {
            command: 'burrow.explainMore',
            title: 'Explain Last Analysis'
        };
        explainAction.isPreferred = true;
        actions.push(explainAction);

        // 2. Load suggestions for the current document
        const result = SharedState.lastAnalysisResult;
        if (result && result.remediation_result && result.remediation_result.suggestions) {
            const currentFileLower = document.uri.fsPath.toLowerCase();
            const matchingSuggestions = result.remediation_result.suggestions
                .map((s: any, idx: number) => ({ s, idx }))
                .filter((item: any) => {
                    const s = item.s;
                    const affectedLower = s.affected_file.toLowerCase();
                    return currentFileLower === affectedLower ||
                           currentFileLower.endsWith(affectedLower.replace(/\\/g, '/')) ||
                           affectedLower.endsWith(currentFileLower.replace(/\\/g, '/'));
                });

            for (const { s, idx } of matchingSuggestions) {
                // Add Preview Action
                const previewAction = new vscode.CodeAction(`Burrow: Preview Patch [${s.risk_level.toUpperCase()}] - ${s.description}`, vscode.CodeActionKind.QuickFix);
                previewAction.command = {
                    command: 'burrow.previewPatch',
                    title: 'Preview Patch',
                    arguments: [s, idx]
                };
                actions.push(previewAction);

                // Add Apply Action
                const applyAction = new vscode.CodeAction(`Burrow: Apply Patch [${s.risk_level.toUpperCase()}] - ${s.description}`, vscode.CodeActionKind.QuickFix);
                applyAction.command = {
                    command: 'burrow.applyPatch',
                    title: 'Apply Patch',
                    arguments: [s, idx]
                };
                actions.push(applyAction);
            }
        }

        return actions;
    }
}
