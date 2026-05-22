# Burrow VSCode Extension

Burrow is a local-first AI debugging intelligence engine. This extension integrates Burrow directly into your editor using native VSCode primitives.

## Features

- **Automated Terminal Monitoring**: Watches your test and build commands, extracts traceback diagnostics automatically, and maps them directly to active editor lines.
- **Visual Diagnostics Bridge**: Color-coded inline diagnostics indicating the root cause, confidence score, and remediation steps.
- **Native Sidebar**: Displays the ingestion logs, ranked root-cause hypotheses, suggested patches, and stack traces.
- **Quick-Fix Code Actions**: Offers options to "Explain Last Analysis", "Preview Patch", or "Apply Patch" via standard editor actions.
- **Rollback Capabilities**: Every applied patch is backed up and can be safely reverted or undone.

## Requirements

- Python 3.9+ with `burrow` installed in your path/virtualenv.

## Extension Settings

This extension contributes the following settings:

* `burrow.backendUrl`: URL of the Burrow FastAPI server (default: `http://localhost:8000`).
* `burrow.autoStartBackend`: Automatically start and manage the backend process.
* `burrow.pythonPath`: Path to the Python executable.
* `burrow.enableAutoPatch`: Allow non-interactive patch writes.
* `burrow.patchMinConfidence`: Minimum confidence threshold to suggest/apply patches.
* `burrow.allowedWritePaths`: Scope-limited write paths relative to the project root.

## License

MIT
