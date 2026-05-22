import * as vscode from 'vscode';
import { StateManager } from './stateManager';
import { BackendConnector } from './backendConnector';
import { DiagnosticsBridge } from './diagnosticsBridge';
import { TerminalWatcher } from './terminalWatcher';
import { CommandHandlers } from './commandHandlers';
import { BurrowTreeView } from './treeView';
import { PatchProvider } from './patchProvider';
import { BurrowHoverProvider } from './hoverProvider';
import { BurrowCodeActionsProvider } from './codeActions';

let backendConnectorInstance: BackendConnector | null = null;

export async function activate(context: vscode.ExtensionContext) {
    console.log('Burrow extension is now active.');

    const stateManager = new StateManager();
    const backendConnector = new BackendConnector(stateManager);
    backendConnectorInstance = backendConnector;
    
    const diagnosticsBridge = new DiagnosticsBridge(stateManager);
    
    // Instantiate Tree View
    const treeView = new BurrowTreeView();
    diagnosticsBridge.setTreeView(treeView);
    
    const commandHandlers = new CommandHandlers(backendConnector, diagnosticsBridge, stateManager);

    // Instantiate and register Patch Provider
    const patchProvider = new PatchProvider();
    const patchRegistration = vscode.workspace.registerTextDocumentContentProvider(
        PatchProvider.scheme,
        patchProvider
    );

    // Instantiate and register Hover Provider
    const hoverProvider = new BurrowHoverProvider();
    const hoverRegistration = vscode.languages.registerHoverProvider(
        { scheme: 'file' },
        hoverProvider
    );

    // Instantiate and register Code Actions Quick Fix Provider
    const codeActionsProvider = new BurrowCodeActionsProvider();
    const codeActionsRegistration = vscode.languages.registerCodeActionsProvider(
        { scheme: 'file' },
        codeActionsProvider,
        { providedCodeActionKinds: BurrowCodeActionsProvider.providedCodeActionKinds }
    );

    // Register all command handlers
    const commands = [
        vscode.commands.registerCommand('burrow.analyzeSelection', () => commandHandlers.analyzeSelection()),
        vscode.commands.registerCommand('burrow.analyzeFile', () => commandHandlers.analyzeFile()),
        vscode.commands.registerCommand('burrow.startBackend', () => commandHandlers.startBackend()),
        vscode.commands.registerCommand('burrow.stopBackend', () => commandHandlers.stopBackend()),
        vscode.commands.registerCommand('burrow.clearDiagnostics', () => commandHandlers.clearDiagnostics()),
        vscode.commands.registerCommand('burrow.previewPatch', (suggestion, idx) => commandHandlers.previewPatch(suggestion, idx)),
        vscode.commands.registerCommand('burrow.applyPatch', (suggestion, idx) => commandHandlers.applyPatch(suggestion, idx)),
        vscode.commands.registerCommand('burrow.explainMore', () => commandHandlers.explainMore()),
        vscode.commands.registerCommand('burrow.openFile', (filePath, lineNumber) => commandHandlers.openFile(filePath, lineNumber))
    ];

    // Register Sidebar Tree View
    const treeViewRegistration = vscode.window.registerTreeDataProvider(
        'burrow.analysisView',
        treeView
    );

    // Add to subscriptions
    context.subscriptions.push(backendConnector);
    context.subscriptions.push(diagnosticsBridge);
    context.subscriptions.push(patchRegistration);
    context.subscriptions.push(hoverRegistration);
    context.subscriptions.push(codeActionsRegistration);
    context.subscriptions.push(treeViewRegistration);
    commands.forEach(cmd => context.subscriptions.push(cmd));

    // Only start terminal watcher when a workspace is actually open
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (workspaceFolders && workspaceFolders.length > 0) {
        const terminalWatcher = new TerminalWatcher(backendConnector, diagnosticsBridge);
        terminalWatcher.start();
        context.subscriptions.push(terminalWatcher);
    }

    // Re-initialize terminal watcher if workspace folders change
    const workspaceChangeDisposable = vscode.workspace.onDidChangeWorkspaceFolders((event) => {
        if (event.added.length > 0) {
            const terminalWatcher = new TerminalWatcher(backendConnector, diagnosticsBridge);
            terminalWatcher.start();
            context.subscriptions.push(terminalWatcher);
            console.log('Burrow: workspace folders changed — terminal watcher restarted.');
        }
    });
    context.subscriptions.push(workspaceChangeDisposable);

    // Defer backend auto-start by 2000ms to avoid blocking editor startup UI thread
    if (stateManager.autoStartBackend) {
        setTimeout(() => {
            backendConnector.startBackend().catch((err: Error) => {
                console.error('Burrow backend auto-start failed:', err);
            });
        }, 2000);
    }
}

export function deactivate() {
    if (backendConnectorInstance) {
        backendConnectorInstance.stopBackend();
    }
}
