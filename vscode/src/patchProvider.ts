import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { SharedState } from './stateManager';

export class PatchProvider implements vscode.TextDocumentContentProvider {
    public static scheme = 'burrow-patch';

    public async provideTextDocumentContent(uri: vscode.Uri, token: vscode.CancellationToken): Promise<string> {
        try {
            const queryParams = new URLSearchParams(uri.query);
            const filePath = queryParams.get('filePath');
            const indexStr = queryParams.get('suggestionIndex');

            if (!filePath || indexStr === null) {
                return '// Burrow error: invalid patch URI parameters';
            }

            const index = parseInt(indexStr, 10);
            const result = SharedState.lastAnalysisResult;

            if (!result || !result.remediation_result || !result.remediation_result.suggestions) {
                return '// Burrow error: no active analysis results found';
            }

            const suggestion = result.remediation_result.suggestions[index];
            if (!suggestion) {
                return `// Burrow error: suggestion index ${index} not found`;
            }

            // Read original content
            let originalContent = '';
            if (fs.existsSync(filePath)) {
                originalContent = await fs.promises.readFile(filePath, 'utf8');
            }

            return this.applyPatch(originalContent, suggestion);
        } catch (err: any) {
            return `// Burrow error applying patch: ${err.message}`;
        }
    }

    /**
     * Applies the patch suggestion to the original content.
     */
    public applyPatch(originalContent: string, suggestion: any): string {
        const preview: string = suggestion.patch_preview || '';
        if (!preview) {
            return originalContent;
        }

        const lines = originalContent.split(/\r?\n/);
        const previewLines = preview.split(/\r?\n/);
        const editRegion = (suggestion.likely_edit_region || '').toLowerCase();

        // 1. Special case: dotenv configuration append
        if (editRegion.includes('append') || editRegion.includes('append to file')) {
            const cleanAdditions = previewLines
                .filter(line => line.startsWith('+'))
                .map(line => line.substring(1).trim());

            if (cleanAdditions.length > 0) {
                return originalContent + (originalContent.endsWith('\n') ? '' : '\n') + cleanAdditions.join('\n') + '\n';
            }
            // If it's a plain block and doesn't start with '+', append as-is
            const cleanPreview = preview.replace(/^```(?:\w+)?\n/, '').replace(/\n```$/, '');
            return originalContent + (originalContent.endsWith('\n') ? '' : '\n') + cleanPreview + '\n';
        }

        // 2. Special case: Prepend (First lines of file)
        if (editRegion.includes('first lines') || editRegion.includes('beginning')) {
            const cleanPreview = preview
                .replace(/^```(?:\w+)?\n/, '')
                .replace(/\n```$/, '')
                .replace(/^\+ /, ''); // strip leading '+' if present
            return cleanPreview + '\n' + originalContent;
        }

        // 3. Special case: Replace a specific line
        const lineMatch = editRegion.match(/line\s+(\d+)/);
        if (lineMatch) {
            const lineNum = parseInt(lineMatch[1], 10);
            const lineIdx = lineNum - 1;
            if (lineIdx >= 0 && lineIdx < lines.length) {
                const cleanLines = previewLines
                    .filter(line => !line.startsWith('-'))
                    .map(line => line.startsWith('+') ? line.substring(1) : line);
                
                const replacement = cleanLines.join('\n').replace(/^```(?:\w+)?\n/, '').replace(/\n```$/, '');
                lines[lineIdx] = replacement;
                return lines.join('\n');
            }
        }

        // 4. Heuristic replacement of exact matching blocks
        // Clean markdown blocks
        const cleanedBlock = preview.replace(/^```(?:\w+)?\n/, '').replace(/\n```$/, '');
        
        // If there's an exact match we could replace, but since it's a preview,
        // let's do a fallback: Append custom suggestion comments if we can't merge cleanly.
        if (originalContent.trim() === '') {
            return cleanedBlock;
        }

        // Fallback: If unified diff is supplied
        if (preview.includes('\n+') || preview.includes('\n-') || preview.startsWith('+ ') || preview.startsWith('- ')) {
            const cleanLines = previewLines
                .filter(line => !line.startsWith('-'))
                .map(line => line.startsWith('+') ? line.substring(1) : line);
            return cleanLines.join('\n');
        }

        return originalContent + '\n\n# Burrow Suggested Remedy:\n' + cleanedBlock + '\n';
    }
}
