import * as vscode from 'vscode';
import * as path from 'path';
import { SharedState } from './stateManager';

export class BurrowTreeItem extends vscode.TreeItem {
    constructor(
        public readonly label: string,
        public readonly collapsibleState: vscode.TreeItemCollapsibleState,
        public readonly description?: string,
        public readonly children?: BurrowTreeItem[],
        public readonly command?: vscode.Command,
        public readonly contextValue?: string
    ) {
        super(label, collapsibleState);
        this.description = description;
        this.contextValue = contextValue;
    }
}

export class BurrowTreeView implements vscode.TreeDataProvider<BurrowTreeItem> {
    private _onDidChangeTreeData: vscode.EventEmitter<BurrowTreeItem | undefined | null | void> = new vscode.EventEmitter<BurrowTreeItem | undefined | null | void>();
    readonly onDidChangeTreeData: vscode.Event<BurrowTreeItem | undefined | null | void> = this._onDidChangeTreeData.event;

    public refresh(): void {
        this._onDidChangeTreeData.fire();
    }

    public getTreeItem(element: BurrowTreeItem): vscode.TreeItem {
        return element;
    }

    public getChildren(element?: BurrowTreeItem): BurrowTreeItem[] {
        const result = SharedState.lastAnalysisResult;

        if (element) {
            return element.children || [];
        }

        // Root elements
        if (!result) {
            return [
                new BurrowTreeItem(
                    'No Active Analysis',
                    vscode.TreeItemCollapsibleState.None,
                    'Run "Burrow: Analyze Active File" or watch terminal to begin'
                )
            ];
        }

        const items: BurrowTreeItem[] = [];

        // 1. Status Category
        const statusChildren: BurrowTreeItem[] = [
            new BurrowTreeItem(`Error Type: ${result.error.error_type}`, vscode.TreeItemCollapsibleState.None),
            new BurrowTreeItem(`Message: ${result.error.message}`, vscode.TreeItemCollapsibleState.None),
            new BurrowTreeItem(`Language: ${result.error.language}`, vscode.TreeItemCollapsibleState.None),
            new BurrowTreeItem(`Confidence Score: ${(result.error.confidence_score * 100).toFixed(0)}%`, vscode.TreeItemCollapsibleState.None)
        ];
        items.push(new BurrowTreeItem('Analysis Status', vscode.TreeItemCollapsibleState.Expanded, '', statusChildren));

        // 2. Root Cause Category
        if (result.rca_result && result.rca_result.hypotheses && result.rca_result.hypotheses.length > 0) {
            const rcaChildren: BurrowTreeItem[] = [];
            for (const hyp of result.rca_result.hypotheses) {
                const originName = path.basename(hyp.origin_file);
                const label = `${hyp.type.toUpperCase()}: ${hyp.root_cause}`;
                
                const fileUri = vscode.Uri.file(hyp.origin_file);
                const fileCommand = {
                    command: 'burrow.openFile',
                    title: 'Open Origin File',
                    arguments: [hyp.origin_file, hyp.line_number || 1]
                };

                const hypDetails: BurrowTreeItem[] = [
                    new BurrowTreeItem(`Reasoning: ${hyp.reasoning_summary}`, vscode.TreeItemCollapsibleState.None),
                    new BurrowTreeItem(`Suggested Fix: ${hyp.safest_fix_direction}`, vscode.TreeItemCollapsibleState.None),
                    new BurrowTreeItem(`Origin File: ${originName}:${hyp.line_number || 1}`, vscode.TreeItemCollapsibleState.None, '', undefined, fileCommand)
                ];

                rcaChildren.push(new BurrowTreeItem(label, vscode.TreeItemCollapsibleState.Collapsed, `Confidence: ${(hyp.confidence_score * 100).toFixed(0)}%`, hypDetails));
            }
            items.push(new BurrowTreeItem('Root Cause Analysis', vscode.TreeItemCollapsibleState.Expanded, '', rcaChildren));
        }

        // 3. Remediation Suggestions Category
        if (result.remediation_result && result.remediation_result.suggestions && result.remediation_result.suggestions.length > 0) {
            const suggestionsChildren: BurrowTreeItem[] = result.remediation_result.suggestions.map((s: any, idx: number) => {
                const previewCommand = {
                    command: 'burrow.previewPatch',
                    title: 'Preview Patch',
                    arguments: [s, idx]
                };

                const details: BurrowTreeItem[] = [
                    new BurrowTreeItem(`Rationale: ${s.rationale}`, vscode.TreeItemCollapsibleState.None),
                    new BurrowTreeItem(`Edit Region: ${s.likely_edit_region}`, vscode.TreeItemCollapsibleState.None),
                    new BurrowTreeItem(`Risk Level: ${s.risk_level}`, vscode.TreeItemCollapsibleState.None)
                ];

                if (s.patch_preview) {
                    details.push(new BurrowTreeItem('Double-click to preview patch', vscode.TreeItemCollapsibleState.None, '', undefined, previewCommand));
                }

                const filename = path.basename(s.affected_file);
                return new BurrowTreeItem(
                    `${s.description} (${filename})`,
                    vscode.TreeItemCollapsibleState.Collapsed,
                    `[${s.risk_level.toUpperCase()}]`,
                    details,
                    previewCommand,
                    'remediationSuggestion'
                );
            });
            items.push(new BurrowTreeItem('Remediation Suggestions', vscode.TreeItemCollapsibleState.Expanded, '', suggestionsChildren));
        }

        // 4. Traceback Category
        if (result.error && result.error.frames && result.error.frames.length > 0) {
            const framesChildren: BurrowTreeItem[] = result.error.frames.map((frame: any) => {
                const filename = path.basename(frame.file_path);
                const fileCommand = {
                    command: 'burrow.openFile',
                    title: 'Jump to Frame Line',
                    arguments: [frame.file_path, frame.line_number || 1]
                };

                return new BurrowTreeItem(
                    `${frame.function_name || 'anonymous'}()`,
                    vscode.TreeItemCollapsibleState.None,
                    `${filename}:${frame.line_number || 1}`,
                    undefined,
                    fileCommand
                );
            });
            items.push(new BurrowTreeItem('Traceback Frames', vscode.TreeItemCollapsibleState.Collapsed, '', framesChildren));
        }

        // 5. Code Smells Category
        if (result.symbol_graph_data && result.symbol_graph_data.smells && result.symbol_graph_data.smells.length > 0) {
            const smellsChildren: BurrowTreeItem[] = result.symbol_graph_data.smells.map((smell: any) => {
                const filename = path.basename(smell.file_path);
                const fileCommand = {
                    command: 'burrow.openFile',
                    title: 'Jump to Smell Line',
                    arguments: [smell.file_path, smell.line_number || 1]
                };

                return new BurrowTreeItem(
                    `${smell.smell_type}: ${smell.message}`,
                    vscode.TreeItemCollapsibleState.None,
                    `${filename}:${smell.line_number}`,
                    undefined,
                    fileCommand
                );
            });
            items.push(new BurrowTreeItem('Detected Code Smells', vscode.TreeItemCollapsibleState.Collapsed, '', smellsChildren));
        }

        return items;
    }
}
