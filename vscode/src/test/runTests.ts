import { run } from 'node:test';
import { spec } from 'node:test/reporters';
import * as path from 'path';

const testStream = run({
    files: [path.resolve(__dirname, 'extension.test.js')]
});

testStream.on('test:fail', () => {
    process.exitCode = 1;
});

testStream.compose(new spec()).pipe(process.stdout);
