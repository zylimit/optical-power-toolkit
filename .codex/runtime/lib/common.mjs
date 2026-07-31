import { createHash, randomBytes } from 'node:crypto';
import { spawn } from 'node:child_process';
import { lstat, mkdir, readFile, realpath, rename, stat, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';

export const TOOL_VERSION = 'codex-base-harness/2.0.0';

export class HarnessError extends Error {
  constructor(message, code = 'HARNESS_ERROR', details = undefined) {
    super(message);
    this.name = 'HarnessError';
    this.code = code;
    this.details = details;
  }
}

export function normalizeLf(value) {
  return String(value).replace(/\r\n?/g, '\n');
}

export function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

export function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

export function contentHash(record) {
  const copy = { ...record };
  delete copy.contentHash;
  return sha256(stableJson(copy));
}

export async function readJson(filePath, { required = true } = {}) {
  let text;
  try {
    text = await readFile(filePath, 'utf8');
  } catch (error) {
    if (!required && error.code === 'ENOENT') return null;
    throw new HarnessError(`Cannot read JSON file: ${filePath}: ${error.message}`, 'JSON_READ_FAILED');
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new HarnessError(`Invalid JSON file: ${filePath}: ${error.message}`, 'JSON_PARSE_FAILED');
  }
}

export async function atomicWrite(filePath, value) {
  await mkdir(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.${randomBytes(6).toString('hex')}.tmp`;
  const body = typeof value === 'string' || Buffer.isBuffer(value)
    ? value
    : `${JSON.stringify(value, null, 2)}\n`;
  try {
    await writeFile(temporary, body, { flag: 'wx' });
    await rename(temporary, filePath);
  } catch (error) {
    await unlink(temporary).catch(() => {});
    throw new HarnessError(`Atomic write failed: ${filePath}: ${error.message}`, 'ATOMIC_WRITE_FAILED');
  }
}

export function redactSecrets(value) {
  let text = String(value ?? '');
  const patterns = [
    /\b(sk|pk|rk|sess)-[A-Za-z0-9_-]{12,}\b/g,
    /\bgh[pousr]_[A-Za-z0-9]{20,}\b/g,
    /\b(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*[=:]\s*[^\s"']+/gi,
    /(authorization\s*:\s*(?:bearer|basic)\s+)[^\s"']+/gi,
    /(password|passwd|token|secret|api[_-]?key)\s*[=:]\s*[^\s,"']+/gi,
    /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g
  ];
  for (const pattern of patterns) {
    text = text.replace(pattern, (match, prefix) => prefix ? `${prefix}[REDACTED]` : '[REDACTED]');
  }
  return text;
}

export function boundedText(value, limit, suffix = '\n...[truncated]') {
  const clean = redactSecrets(value);
  if (clean.length <= limit) return clean;
  return `${clean.slice(0, Math.max(0, limit - suffix.length))}${suffix}`;
}

export function toPosix(value) {
  return value.split(path.sep).join('/').replace(/^\.\//, '');
}

export function normalizeRepoPath(value) {
  const normalized = toPosix(path.posix.normalize(toPosix(String(value))));
  if (!normalized || normalized === '.' || normalized.startsWith('../') || path.posix.isAbsolute(normalized)) {
    throw new HarnessError(`Unsafe repository path: ${value}`, 'UNSAFE_PATH');
  }
  return normalized;
}

export function isPathInside(parent, child) {
  const relative = path.relative(parent, child);
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

export async function resolveForWrite(repoRoot, inputPath) {
  const absolute = path.resolve(repoRoot, String(inputPath));
  const realRoot = await realpath(repoRoot);
  if (!isPathInside(realRoot, absolute)) {
    throw new HarnessError(`Write target is outside the workspace: ${inputPath}`, 'OUTSIDE_WORKSPACE');
  }
  let cursor = absolute;
  while (cursor !== path.dirname(cursor)) {
    try {
      const existing = await realpath(cursor);
      if (!isPathInside(realRoot, existing)) {
        throw new HarnessError(`Write target resolves outside the workspace: ${inputPath}`, 'OUTSIDE_WORKSPACE');
      }
      return { absolute, realRoot, existingAncestor: existing };
    } catch (error) {
      if (error instanceof HarnessError) throw error;
      if (error.code !== 'ENOENT') throw error;
      cursor = path.dirname(cursor);
    }
  }
  throw new HarnessError(`Cannot resolve a safe ancestor for: ${inputPath}`, 'UNSAFE_PATH');
}

export async function fileDigest(filePath) {
  try {
    const info = await lstat(filePath);
    if (info.isSymbolicLink()) return `symlink:${await realpath(filePath)}`;
    if (!info.isFile()) return `${info.mode}:${info.size}`;
    return sha256(await readFile(filePath));
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

export async function runProcess(executable, args, options = {}) {
  const started = Date.now();
  const timeoutMs = options.timeoutMs ?? 30000;
  const maxOutput = options.maxOutput ?? 200000;
  return await new Promise((resolve) => {
    let stdout = Buffer.alloc(0);
    let stderr = Buffer.alloc(0);
    let settled = false;
    let timedOut = false;
    let child;
    try {
      child = spawn(executable, args, {
        cwd: options.cwd,
        env: options.env ?? process.env,
        shell: options.shell ?? false,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe']
      });
    } catch (error) {
      resolve({ status: 'BLOCKED', error, exitCode: null, signal: null, stdout: '', stderr: '', durationMs: Date.now() - started });
      return;
    }
    const append = (current, chunk) => Buffer.concat([current, chunk]).subarray(0, maxOutput);
    child.stdout.on('data', (chunk) => { stdout = append(stdout, chunk); });
    child.stderr.on('data', (chunk) => { stderr = append(stderr, chunk); });
    child.on('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ status: 'BLOCKED', error, exitCode: null, signal: null, stdout: stdout.toString(), stderr: stderr.toString(), durationMs: Date.now() - started });
    });
    child.on('close', (exitCode, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({
        status: timedOut ? 'BLOCKED' : exitCode === 0 ? 'PASS' : 'FAIL',
        exitCode,
        signal,
        stdout: stdout.toString(),
        stderr: stderr.toString(),
        durationMs: Date.now() - started,
        timedOut
      });
    });
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGTERM');
      setTimeout(() => child.kill('SIGKILL'), 1000).unref();
    }, timeoutMs);
    timer.unref();
  });
}

export async function pathExists(filePath) {
  try {
    await stat(filePath);
    return true;
  } catch (error) {
    if (error.code === 'ENOENT') return false;
    throw error;
  }
}

export function parseCliArgs(argv) {
  const positional = [];
  const flags = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith('--')) {
      positional.push(token);
      continue;
    }
    const [rawKey, inline] = token.slice(2).split(/=(.*)/s, 2);
    if (inline !== undefined) {
      flags[rawKey] = inline;
    } else if (argv[index + 1] && !argv[index + 1].startsWith('--')) {
      flags[rawKey] = argv[++index];
    } else {
      flags[rawKey] = true;
    }
  }
  return { positional, flags };
}
