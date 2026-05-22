import * as vscode from 'vscode';

export class SharedState {
    public static lastAnalysisResult: any = null;
}

export class StateManager {
    public get backendUrl(): string {
        return vscode.workspace.getConfiguration('burrow').get<string>('backendUrl') || 'http://localhost:8000';
    }

    public get autoStartBackend(): boolean {
        return vscode.workspace.getConfiguration('burrow').get<boolean>('autoStartBackend', true);
    }

    public get pythonPath(): string {
        return vscode.workspace.getConfiguration('burrow').get<string>('pythonPath') || 'python';
    }

    public get logLevel(): string {
        return vscode.workspace.getConfiguration('burrow').get<string>('logLevel') || 'INFO';
    }

    public get enableAutoPatch(): boolean {
        return vscode.workspace.getConfiguration('burrow').get<boolean>('enableAutoPatch', false);
    }

    public get patchMinConfidence(): number {
        return vscode.workspace.getConfiguration('burrow').get<number>('patchMinConfidence', 0.70);
    }

    public get allowedWritePaths(): string {
        return vscode.workspace.getConfiguration('burrow').get<string>('allowedWritePaths') || '';
    }

    public get workspaceRoot(): string | undefined {
        const folders = vscode.workspace.workspaceFolders;
        if (folders && folders.length > 0) {
            return folders[0].uri.fsPath;
        }
        return undefined;
    }
}
