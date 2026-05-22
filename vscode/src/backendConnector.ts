import * as vscode from 'vscode';
import * as child_process from 'child_process';
import * as path from 'path';
import * as http from 'http';
import * as https from 'https';
import { StateManager } from './stateManager';

export class BackendConnector {
    private stateManager: StateManager;
    private backendProcess: child_process.ChildProcess | null = null;
    private outputChannel: vscode.OutputChannel;

    constructor(stateManager: StateManager) {
        this.stateManager = stateManager;
        this.outputChannel = vscode.window.createOutputChannel('Burrow Backend');
    }

    /**
     * Checks if the backend API server is healthy.
     */
    public async checkHealth(): Promise<boolean> {
        const url = `${this.stateManager.backendUrl}/health`;
        try {
            const res = await this.httpRequest(url, 'GET');
            return res && res.status === 'ok';
        } catch (err) {
            return false;
        }
    }

    /**
     * Starts the FastAPI backend process if it's not already running.
     */
    public async startBackend(): Promise<boolean> {
        if (this.backendProcess) {
            this.outputChannel.appendLine('Backend is already running or start is in progress.');
            return true;
        }

        const isRunning = await this.checkHealth();
        if (isRunning) {
            this.outputChannel.appendLine('Burrow backend is already running on another process.');
            return true;
        }

        const workspaceRoot = this.stateManager.workspaceRoot;
        if (!workspaceRoot) {
            vscode.window.showErrorMessage('Burrow requires an open workspace folder to start the backend.');
            return false;
        }

        const pythonPath = this.stateManager.pythonPath;
        const backendUrl = this.stateManager.backendUrl;
        const logLevel = this.stateManager.logLevel;

        let host = '127.0.0.1';
        let port = '8000';
        try {
            const parsedUrl = new URL(backendUrl);
            host = parsedUrl.hostname || '127.0.0.1';
            port = parsedUrl.port || '8000';
        } catch (err) {
            this.outputChannel.appendLine(`Failed to parse backendUrl: ${backendUrl}, falling back to 127.0.0.1:8000`);
        }

        const pythonScriptPath = path.join(workspaceRoot, 'src', 'burrow', 'cli', 'main.py');
        const args = [
            pythonScriptPath,
            'api',
            '--host', host,
            '--port', port,
            '--log-level', logLevel.toLowerCase()
        ];

        this.outputChannel.appendLine(`Starting backend: ${pythonPath} ${args.join(' ')}`);
        
        const env = {
            ...process.env,
            PYTHONPATH: path.join(workspaceRoot, 'src')
        };

        try {
            this.backendProcess = child_process.spawn(pythonPath, args, {
                cwd: workspaceRoot,
                env: env
            });

            this.backendProcess.stdout?.on('data', (data) => {
                this.outputChannel.append(data.toString());
            });

            this.backendProcess.stderr?.on('data', (data) => {
                this.outputChannel.append(data.toString());
            });

            this.backendProcess.on('close', (code) => {
                this.outputChannel.appendLine(`Backend process exited with code ${code}`);
                this.backendProcess = null;
            });

            // Wait and check health
            for (let i = 0; i < 10; i++) {
                await new Promise((resolve) => setTimeout(resolve, 1000));
                const healthy = await this.checkHealth();
                if (healthy) {
                    this.outputChannel.appendLine('Burrow backend started and verified healthy.');
                    vscode.window.showInformationMessage('Burrow backend service is running.');
                    return true;
                }
            }

            vscode.window.showWarningMessage('Burrow backend spawned but health checks are failing. Check the Burrow Backend output channel.');
            return false;
        } catch (err: any) {
            this.outputChannel.appendLine(`Failed to start backend: ${err.message}`);
            vscode.window.showErrorMessage(`Failed to start Burrow backend: ${err.message}`);
            this.backendProcess = null;
            return false;
        }
    }

    /**
     * Terminate the backend process if running.
     */
    public stopBackend(): void {
        if (this.backendProcess) {
            this.outputChannel.appendLine('Stopping Burrow backend process...');
            this.backendProcess.kill();
            this.backendProcess = null;
            vscode.window.showInformationMessage('Burrow backend process terminated.');
        } else {
            this.outputChannel.appendLine('No active backend process to stop.');
        }
    }

    /**
     * Sends error traceback text for analysis.
     */
    public async analyze(content: string): Promise<any> {
        const url = `${this.stateManager.backendUrl}/api/v1/analyze`;
        const body = {
            content: content,
            project_root: this.stateManager.workspaceRoot
        };
        this.outputChannel.appendLine('Sending payload to /api/v1/analyze...');
        return this.httpRequest(url, 'POST', body);
    }

    /**
     * Native HTTP/HTTPS request helper.
     */
    private httpRequest(urlStr: string, method: string, body?: any): Promise<any> {
        return new Promise((resolve, reject) => {
            const url = new URL(urlStr);
            const protocol = url.protocol === 'https:' ? https : http;
            const options: http.RequestOptions = {
                method: method,
                headers: body ? {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(JSON.stringify(body))
                } : {}
            };

            const req = protocol.request(url, options, (res) => {
                let data = '';
                res.on('data', (chunk) => {
                    data += chunk;
                });
                res.on('end', () => {
                    if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
                        try {
                            resolve(JSON.parse(data));
                        } catch (e) {
                            resolve(data);
                        }
                    } else {
                        reject(new Error(`HTTP ${res.statusCode}: ${data}`));
                    }
                });
            });

            req.on('error', (err) => {
                reject(err);
            });

            if (body) {
                req.write(JSON.stringify(body));
            }
            req.end();
        });
    }

    public dispose(): void {
        this.stopBackend();
        this.outputChannel.dispose();
    }
}
