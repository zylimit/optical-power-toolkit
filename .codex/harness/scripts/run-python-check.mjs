#!/usr/bin/env node
import { spawnSync } from 'node:child_process';

const commands = {
  compile: ['-m', 'compileall', '-q', 'scripts', 'server'],
  test: ['-m', 'pytest', '-q', '-p', 'no:cacheprovider']
};

const action = process.argv[2];
if (!commands[action]) {
  process.stderr.write(`Usage: node ${process.argv[1]} <compile|test>\n`);
  process.exit(64);
}

const candidates = process.platform === 'win32'
  ? [{ executable: 'py', prefix: ['-3'] }, { executable: 'python', prefix: [] }]
  : [{ executable: 'python3', prefix: [] }, { executable: 'python', prefix: [] }];

const candidate = candidates.find(({ executable, prefix }) => {
  const result = spawnSync(executable, [...prefix, '--version'], { stdio: 'ignore', windowsHide: true });
  return result.status === 0;
});

if (!candidate) {
  process.stderr.write(`No supported Python interpreter found for ${process.platform}. Tried: ${candidates.map(({ executable }) => executable).join(', ')}\n`);
  process.exit(127);
}

const result = spawnSync(candidate.executable, [...candidate.prefix, ...commands[action]], {
  stdio: 'inherit',
  windowsHide: true
});
if (result.error) {
  process.stderr.write(`${result.error.message}\n`);
  process.exit(1);
}
process.exit(result.status ?? 1);
