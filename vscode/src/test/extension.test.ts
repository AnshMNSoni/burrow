// Ensure mockVscode is imported first to monkeypatch the 'vscode' module
import './mockVscode';
import { mockConfigs, mockWorkspaceFolders, mockVscode } from './mockVscode';

import test from 'node:test';
import assert from 'node:assert';
import * as path from 'path';

import { StateManager, SharedState } from '../stateManager';
import { DiagnosticsBridge, AnalysisResult } from '../diagnosticsBridge';
import { PatchProvider } from '../patchProvider';

test('StateManager settings extraction and workspace resolution', async (t) => {
    const manager = new StateManager();

    await t.test('reads default values when no config is set', () => {
        mockConfigs.clear();
        assert.strictEqual(manager.backendUrl, 'http://localhost:8000');
        assert.strictEqual(manager.autoStartBackend, true);
        assert.strictEqual(manager.pythonPath, 'python');
        assert.strictEqual(manager.logLevel, 'INFO');
        assert.strictEqual(manager.enableAutoPatch, false);
        assert.strictEqual(manager.patchMinConfidence, 0.70);
        assert.strictEqual(manager.allowedWritePaths, '');
        assert.strictEqual(manager.workspaceRoot, undefined);
    });

    await t.test('reads configured values', () => {
        mockConfigs.clear();
        mockConfigs.set('burrow.backendUrl', 'http://127.0.0.1:9000');
        mockConfigs.set('burrow.autoStartBackend', false);
        mockConfigs.set('burrow.pythonPath', '/usr/bin/python3');
        mockConfigs.set('burrow.logLevel', 'DEBUG');
        mockConfigs.set('burrow.enableAutoPatch', true);
        mockConfigs.set('burrow.patchMinConfidence', 0.85);
        mockConfigs.set('burrow.allowedWritePaths', 'src/,tests/');

        assert.strictEqual(manager.backendUrl, 'http://127.0.0.1:9000');
        assert.strictEqual(manager.autoStartBackend, false);
        assert.strictEqual(manager.pythonPath, '/usr/bin/python3');
        assert.strictEqual(manager.logLevel, 'DEBUG');
        assert.strictEqual(manager.enableAutoPatch, true);
        assert.strictEqual(manager.patchMinConfidence, 0.85);
        assert.strictEqual(manager.allowedWritePaths, 'src/,tests/');
    });

    await t.test('resolves workspaceRoot if workspaceFolders exist', () => {
        mockWorkspaceFolders.length = 0;
        mockWorkspaceFolders.push({
            uri: mockVscode.Uri.file('/my/project/root'),
            name: 'root',
            index: 0
        });

        assert.strictEqual(manager.workspaceRoot, mockVscode.Uri.file('/my/project/root').fsPath);

        // Cleanup
        mockWorkspaceFolders.length = 0;
    });
});

test('DiagnosticsBridge diagnostic mapping', async (t) => {
    const manager = new StateManager();
    const bridge = new DiagnosticsBridge(manager);
    const testRoot = path.resolve('my-project');

    // Setup workspace root for path resolving
    mockWorkspaceFolders.length = 0;
    mockWorkspaceFolders.push({
        uri: mockVscode.Uri.file(testRoot),
        name: 'project',
        index: 0
    });

    await t.test('maps traceback frames correctly', () => {
        bridge.clear();

        const result: AnalysisResult = {
            error: {
                error_type: 'ZeroDivisionError',
                message: 'division by zero',
                language: 'python',
                raw_input: '',
                confidence_score: 1.0,
                frames: [
                    {
                        file_path: 'app.py',
                        line_number: 10,
                        column_number: 4,
                        function_name: 'divide',
                        raw_line: '    return x / y',
                        is_application_code: true
                    },
                    {
                        file_path: 'lib/helpers.py',
                        line_number: 5,
                        raw_line: '    helpers.run()',
                        is_application_code: false
                    }
                ]
            }
        };

        bridge.updateDiagnostics(result);

        // SharedState should track the last analysis result
        assert.deepEqual(SharedState.lastAnalysisResult, result);

        // Inspect diagnostic collection stores
        const collection: any = (bridge as any).diagnosticCollection;
        const store = collection.getDiagnostics();

        // 1. Check app.py diagnostic
        const appUriStr = mockVscode.Uri.file(path.resolve(testRoot, 'app.py')).toString();
        const appDiagnostics = store.get(appUriStr);
        assert.ok(appDiagnostics);
        assert.strictEqual(appDiagnostics.length, 1);
        const diag1 = appDiagnostics[0];
        assert.ok(diag1.message.includes('ZeroDivisionError'));
        assert.ok(diag1.message.includes('divide'));
        assert.strictEqual(diag1.range.start.line, 9); // 0-indexed
        assert.strictEqual(diag1.range.start.character, 3); // 0-indexed
        assert.strictEqual(diag1.severity, mockVscode.DiagnosticSeverity.Error); // Application code is error

        // 2. Check lib/helpers.py diagnostic
        const helpersUriStr = mockVscode.Uri.file(path.resolve(testRoot, 'lib/helpers.py')).toString();
        const helpersDiagnostics = store.get(helpersUriStr);
        assert.ok(helpersDiagnostics);
        assert.strictEqual(helpersDiagnostics.length, 1);
        const diag2 = helpersDiagnostics[0];
        assert.strictEqual(diag2.range.start.line, 4); // 0-indexed
        assert.strictEqual(diag2.range.start.character, 0);
        assert.strictEqual(diag2.severity, mockVscode.DiagnosticSeverity.Warning); // Non-app code is warning
    });

    await t.test('maps RCA results correctly', () => {
        bridge.clear();

        const result: AnalysisResult = {
            error: {
                error_type: 'ValueError',
                message: 'bad config',
                language: 'python',
                raw_input: '',
                confidence_score: 0.9,
                frames: []
            },
            rca_result: {
                hypotheses: [
                    {
                        type: 'Configuration Mismatch',
                        root_cause: 'DATABASE_URL env var not loaded',
                        origin_file: 'config.py',
                        line_number: 12,
                        probable_impacted_modules: ['db.py'],
                        reasoning_summary: 'We saw env load failed',
                        safest_fix_direction: 'Add default string to config',
                        confidence_score: 0.95
                    }
                ],
                propagation_chain: []
            }
        };

        bridge.updateDiagnostics(result);

        const collection: any = (bridge as any).diagnosticCollection;
        const store = collection.getDiagnostics();

        const configUriStr = mockVscode.Uri.file(path.resolve(testRoot, 'config.py')).toString();
        const diagnostics = store.get(configUriStr);
        assert.ok(diagnostics);
        assert.strictEqual(diagnostics.length, 1);
        const diag = diagnostics[0];
        assert.strictEqual(diag.source, 'Burrow RCA');
        assert.strictEqual(diag.code, 'root_cause');
        assert.ok(diag.message.includes('DATABASE_URL'));
        assert.strictEqual(diag.range.start.line, 11);
    });

    await t.test('maps code smells correctly', () => {
        bridge.clear();

        const result: AnalysisResult = {
            error: {
                error_type: 'RuntimeError',
                message: 'failed',
                language: 'python',
                raw_input: '',
                confidence_score: 0.8,
                frames: []
            },
            symbol_graph_data: {
                nodes: [],
                edges: [],
                smells: [
                    {
                        smell_type: 'Complexity',
                        message: 'method divide_and_conquer has complexity 25',
                        file_path: 'maths.py',
                        line_number: 50,
                        severity: 'Warning'
                    }
                ]
            }
        };

        bridge.updateDiagnostics(result);

        const collection: any = (bridge as any).diagnosticCollection;
        const store = collection.getDiagnostics();

        const mathsUriStr = mockVscode.Uri.file(path.resolve(testRoot, 'maths.py')).toString();
        const diagnostics = store.get(mathsUriStr);
        assert.ok(diagnostics);
        assert.strictEqual(diagnostics.length, 1);
        const diag = diagnostics[0];
        assert.strictEqual(diag.source, 'Burrow Symbol Analyzer');
        assert.strictEqual(diag.code, 'Complexity');
        assert.strictEqual(diag.severity, mockVscode.DiagnosticSeverity.Warning);
        assert.strictEqual(diag.range.start.line, 49);
    });

    mockWorkspaceFolders.length = 0;
});

test('PatchProvider applyPatch functionality', async (t) => {
    const provider = new PatchProvider();

    await t.test('appends lines for dotenv append region', () => {
        const original = 'PORT=3000\nHOST=localhost\n';
        const suggestion = {
            likely_edit_region: 'append to file',
            patch_preview: '+DATABASE_URL=postgres://localhost:5432\n+DEBUG=true\n'
        };

        const result = provider.applyPatch(original, suggestion);
        assert.strictEqual(result, 'PORT=3000\nHOST=localhost\nDATABASE_URL=postgres://localhost:5432\nDEBUG=true\n');
    });

    await t.test('prepends lines for beginning of file region', () => {
        const original = 'console.log("main");';
        const suggestion = {
            likely_edit_region: 'first lines of file',
            patch_preview: '```typescript\nimport { logger } from "./logger";\n```'
        };

        const result = provider.applyPatch(original, suggestion);
        assert.strictEqual(result, 'import { logger } from "./logger";\nconsole.log("main");');
    });

    await t.test('replaces specific line for line region', () => {
        const original = 'const x = 1;\nconst y = 2;\nconst z = 3;';
        const suggestion = {
            likely_edit_region: 'replace line 2',
            patch_preview: '+const y = 20;'
        };

        const result = provider.applyPatch(original, suggestion);
        assert.strictEqual(result, 'const x = 1;\nconst y = 20;\nconst z = 3;');
    });

    await t.test('falls back to unified diff clean lines when other patterns missing', () => {
        const original = 'def run():\n    print("start")\n    print("end")';
        const suggestion = {
            likely_edit_region: 'somewhere',
            patch_preview: ' def run():\n+    logger.info("running")\n-    print("start")\n     print("end")'
        };

        const result = provider.applyPatch(original, suggestion);
        // Fallback filter: removes '-', strips '+' prefix
        assert.strictEqual(result, ' def run():\n    logger.info("running")\n     print("end")');
    });
});
