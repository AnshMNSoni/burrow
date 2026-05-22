import * as vscode from 'vscode';
import { StateManager } from './stateManager';
import { BackendConnector } from './backendConnector';
import { DiagnosticsBridge } from './diagnosticsBridge';
import { TerminalWatcher } from './terminalWatcher';
import { CommandHandlers } from './commandHandlers';

let backendConnectorInstance: BackendConnector | null = null;

export async function activate(context: vscode.ExtensionContext) {
    console.log('Burrow extension is now active.');

    const stateManager = new StateManager();
    const backendConnector = new BackendConnector(stateManager);
    backendConnectorInstance = backendConnector;
    const diagnosticsBridge = new DiagnosticsBridge(stateManager);
    const terminalWatcher = new TerminalWatcher(backendConnector, diagnosticsBridge);
    const commandHandlers = new CommandHandlers(backendConnector, diagnosticsBridge);

    // Register commands
    const commands = [
        vscode.commands.registerCommand('burrow.analyzeSelection', () => commandHandlers.analyzeSelection()),
        vscode.commands.registerCommand('burrow.analyzeFile', () => commandHandlers.analyzeFile()),
        vscode.commands.registerCommand('burrow.startBackend', () => commandHandlers.startBackend()),
        vscode.commands.registerCommand('burrow.stopBackend', () => commandHandlers.stopBackend()),
        vscode.commands.registerCommand('burrow.clearDiagnostics', () => commandHandlers.clearDiagnostics())
    ];

    context.subscriptions.push(backendConnector);
    context.subscriptions.push(diagnosticsBridge);
    context.subscriptions.push(terminalWatcher);
    commands.forEach(cmd => context.subscriptions.push(cmd));

    // Initialize terminal watching
    terminalWatcher.start();

    // Check if backend autostart is enabled
    if (stateManager.autoStartBackend) {
        // Run in background without blocking extension activation
        backendConnector.startBackend().catch((err) => {
            console.error('Burrow backend auto-start failed:', err);
        });
    }
}

export function deactivate() {
    if (backendConnectorInstance) {
        backendConnectorInstance.stopBackend();
    }
}
