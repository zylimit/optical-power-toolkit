import { randomUUID } from 'node:crypto';
import { mkdir, open, readFile, stat, unlink } from 'node:fs/promises';
import path from 'node:path';
import { atomicWrite, HarnessError, readJson } from './common.mjs';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function withFileLock(lockPath, options, callback) {
  const timeoutMs = options.timeoutMs ?? 15000;
  const staleMs = options.staleMs ?? 120000;
  const pollMs = options.pollMs ?? 25;
  const started = Date.now();
  await mkdir(path.dirname(lockPath), { recursive: true });
  let handle;
  const ownerToken = randomUUID();
  while (!handle) {
    try {
      handle = await open(lockPath, 'wx');
      await handle.writeFile(JSON.stringify({ pid: process.pid, ownerToken, createdAt: new Date().toISOString() }));
    } catch (error) {
      if (error.code !== 'EEXIST') throw new HarnessError(`Cannot acquire lock ${lockPath}: ${error.message}`, 'LOCK_FAILED');
      const age = await stat(lockPath).then((info) => Date.now() - info.mtimeMs).catch(() => 0);
      if (age > staleMs && !(await lockOwnerAlive(lockPath))) {
        await unlink(lockPath).catch(() => {});
        continue;
      }
      if (Date.now() - started >= timeoutMs) throw new HarnessError(`Timed out waiting for lock: ${lockPath}`, 'LOCK_TIMEOUT');
      await sleep(pollMs);
    }
  }
  try {
    return await callback();
  } finally {
    await handle.close().catch(() => {});
    try {
      const current = JSON.parse(await readFile(lockPath, 'utf8'));
      if (current.ownerToken === ownerToken) await unlink(lockPath);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
}

async function lockOwnerAlive(lockPath) {
  try {
    const value = JSON.parse(await readFile(lockPath, 'utf8'));
    if (!Number.isInteger(value.pid) || value.pid <= 0) return false;
    try { process.kill(value.pid, 0); return true; }
    catch (error) { return error.code === 'EPERM'; }
  } catch {
    return false;
  }
}

export function stateFile(config, relativeName) {
  if (path.isAbsolute(relativeName) || relativeName.split(/[\\/]/).includes('..')) {
    throw new HarnessError(`Unsafe state path: ${relativeName}`, 'UNSAFE_STATE_PATH');
  }
  return path.join(config.statePath, relativeName);
}

export async function readState(config, relativeName, defaultValue = undefined) {
  const filePath = stateFile(config, relativeName);
  const value = await readJson(filePath, { required: false });
  if (value === null) return defaultValue;
  return value;
}

export async function writeState(config, relativeName, value) {
  const filePath = stateFile(config, relativeName);
  const lockPath = `${filePath}.lock`;
  return withFileLock(lockPath, config.locks, async () => {
    await atomicWrite(filePath, value);
    return value;
  });
}

export async function updateState(config, relativeName, defaultValue, updater) {
  const filePath = stateFile(config, relativeName);
  const lockPath = `${filePath}.lock`;
  return withFileLock(lockPath, config.locks, async () => {
    let current = defaultValue;
    try {
      const body = await readFile(filePath, 'utf8');
      current = JSON.parse(body);
    } catch (error) {
      if (error.code !== 'ENOENT') {
        if (error instanceof SyntaxError) throw new HarnessError(`Corrupt state file: ${filePath}: ${error.message}`, 'STATE_CORRUPT');
        throw error;
      }
    }
    const next = await updater(current);
    if (next === undefined) throw new HarnessError(`State updater returned undefined for ${relativeName}`, 'STATE_UPDATE_FAILED');
    await atomicWrite(filePath, next);
    return next;
  });
}

export async function appendLedger(config, relativeName, entry) {
  return updateState(config, relativeName, { version: 1, entries: [] }, (ledger) => {
    if (ledger.version !== 1 || !Array.isArray(ledger.entries)) throw new HarnessError(`Invalid ledger: ${relativeName}`, 'STATE_CORRUPT');
    return { ...ledger, entries: [...ledger.entries, entry] };
  });
}
