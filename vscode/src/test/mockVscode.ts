import Module from 'module';

export class Position {
    constructor(public readonly line: number, public readonly character: number) {}
}

export class Range {
    public readonly start: Position;
    public readonly end: Position;

    constructor(start: Position, end: Position);
    constructor(startLine: number, startChar: number, endLine: number, endChar: number);
    constructor(
        startOrLine: Position | number,
        endOrChar: Position | number,
        endLine?: number,
        endChar?: number
    ) {
        if (startOrLine instanceof Position && endOrChar instanceof Position) {
            this.start = startOrLine;
            this.end = endOrChar;
        } else if (
            typeof startOrLine === 'number' &&
            typeof endOrChar === 'number' &&
            typeof endLine === 'number' &&
            typeof endChar === 'number'
        ) {
            this.start = new Position(startOrLine, endOrChar);
            this.end = new Position(endLine, endChar);
        } else {
            this.start = new Position(0, 0);
            this.end = new Position(0, 0);
        }
    }
}

export class Diagnostic {
    public source?: string;
    public code?: string | number | { value: string | number; target: any };

    constructor(
        public readonly range: Range,
        public readonly message: string,
        public readonly severity: number = 0
    ) {}
}

export enum DiagnosticSeverity {
    Error = 0,
    Warning = 1,
    Information = 2,
    Hint = 3
}

export class Uri {
    public readonly scheme: string;
    public readonly authority: string;
    public readonly path: string;
    public readonly query: string;
    public readonly fragment: string;

    private constructor(scheme: string, authority: string, path: string, query: string, fragment: string) {
        this.scheme = scheme;
        this.authority = authority;
        this.path = path;
        this.query = query;
        this.fragment = fragment;
    }

    public get fsPath(): string {
        if (this.scheme === 'file') {
            // Very basic windows/posix path logic
            return this.path.startsWith('/') && process.platform === 'win32'
                ? this.path.substring(1).replace(/\//g, '\\')
                : this.path;
        }
        return this.path;
    }

    public static file(pathStr: string): Uri {
        const normalizedPath = pathStr.replace(/\\/g, '/');
        const finalPath = normalizedPath.startsWith('/') ? normalizedPath : '/' + normalizedPath;
        return new Uri('file', '', finalPath, '', '');
    }

    public static parse(value: string): Uri {
        try {
            const url = new URL(value);
            return new Uri(
                url.protocol.replace(':', ''),
                url.host,
                url.pathname,
                url.search.replace(/^\?/, ''),
                url.hash.replace(/^#/, '')
            );
        } catch {
            const parts = value.split(':');
            const scheme = parts[0] || 'file';
            const rest = parts.slice(1).join(':');
            const [pathAndQuery, fragment] = rest.split('#');
            const [path, query] = (pathAndQuery || '').split('?');
            return new Uri(scheme, '', path, query || '', fragment || '');
        }
    }

    public toString(): string {
        const queryStr = this.query ? `?${this.query}` : '';
        const fragmentStr = this.fragment ? `#${this.fragment}` : '';
        return `${this.scheme}://${this.authority}${this.path}${queryStr}${fragmentStr}`;
    }
}

class DiagnosticCollectionMock {
    public readonly name: string;
    public readonly store = new Map<string, Diagnostic[]>();

    constructor(name: string) {
        this.name = name;
    }

    public set(uri: Uri, diagnostics: Diagnostic[] | undefined): void {
        if (diagnostics) {
            this.store.set(uri.toString(), diagnostics);
        } else {
            this.store.delete(uri.toString());
        }
    }

    public delete(uri: Uri): void {
        this.store.delete(uri.toString());
    }

    public clear(): void {
        this.store.clear();
    }

    public dispose(): void {
        this.clear();
    }

    public getDiagnostics(): Map<string, Diagnostic[]> {
        return this.store;
    }
}

// Global configs state for tests to manipulate/mock
export const mockConfigs = new Map<string, any>();
export const mockWorkspaceFolders: any[] = [];

export const mockVscode = {
    Position,
    Range,
    Diagnostic,
    DiagnosticSeverity,
    Uri,
    languages: {
        createDiagnosticCollection(name: string) {
            return new DiagnosticCollectionMock(name);
        }
    },
    workspace: {
        get workspaceFolders() {
            return mockWorkspaceFolders.length > 0 ? mockWorkspaceFolders : undefined;
        },
        getConfiguration(section: string) {
            return {
                get(key: string, defaultValue?: any) {
                    const fullKey = `${section}.${key}`;
                    if (mockConfigs.has(fullKey)) {
                        return mockConfigs.get(fullKey);
                    }
                    return defaultValue;
                },
                update(key: string, value: any) {
                    const fullKey = `${section}.${key}`;
                    mockConfigs.set(fullKey, value);
                }
            };
        }
    }
};

// Monkeypatch node's module resolver to return this mock for 'vscode'
const originalResolveFilename = (Module as any)._resolveFilename;
(Module as any)._resolveFilename = function (request: string, parent: any, isMain: boolean, options: any) {
    if (request === 'vscode') {
        return 'vscode';
    }
    return originalResolveFilename.apply(this, arguments);
};

require.cache['vscode'] = {
    id: 'vscode',
    filename: 'vscode',
    loaded: true,
    exports: mockVscode,
    parent: null,
    children: [],
    paths: []
} as any;
