import path from 'node:path';
import { realpath } from 'node:fs/promises';
import { HarnessError, isPathInside, readJson } from './common.mjs';

const TOP_LEVEL = new Set(['version', 'stateDir', 'catalogFile', 'verificationFile', 'outputLimits', 'context', 'catalog', 'locks', 'security']);

function assertObject(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new HarnessError(`${label} must be an object`, 'CONFIG_INVALID');
  }
}

function assertKnownFields(value, allowed, label) {
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) throw new HarnessError(`Unknown ${label} field: ${key}`, 'CONFIG_UNKNOWN_FIELD');
  }
}

function assertPositiveInt(value, label) {
  if (!Number.isInteger(value) || value <= 0) throw new HarnessError(`${label} must be a positive integer`, 'CONFIG_INVALID');
}

export function validateHarnessConfig(config) {
  assertObject(config, 'config');
  assertKnownFields(config, TOP_LEVEL, 'config');
  if (config.version !== 1) throw new HarnessError('config.version must equal 1', 'CONFIG_INVALID');
  for (const field of ['stateDir', 'catalogFile', 'verificationFile']) {
    if (typeof config[field] !== 'string' || !config[field]) throw new HarnessError(`${field} must be a non-empty string`, 'CONFIG_INVALID');
    if (path.isAbsolute(config[field]) || config[field].split(/[\\/]/).includes('..')) throw new HarnessError(`${field} must stay inside the repository`, 'CONFIG_INVALID');
  }
  for (const [section, fields] of Object.entries({
    outputLimits: ['hookChars', 'modelChars', 'evidenceChars'],
    context: ['totalChars', 'fileChars', 'diffChars', 'maxFiles'],
    catalog: ['maxTrackedPaths', 'maxChangedPaths'],
    locks: ['timeoutMs', 'staleMs', 'pollMs']
  })) {
    assertObject(config[section], section);
    assertKnownFields(config[section], new Set(fields), section);
    for (const field of fields) assertPositiveInt(config[section][field], `${section}.${field}`);
  }
  assertObject(config.security, 'security');
  assertKnownFields(config.security, new Set(['dependencyDirs', 'secretNames', 'secretExtensions', 'allowedSecretTemplates']), 'security');
  for (const field of ['dependencyDirs', 'secretNames', 'secretExtensions', 'allowedSecretTemplates']) {
    if (!Array.isArray(config.security[field]) || config.security[field].some((item) => typeof item !== 'string' || !item)) {
      throw new HarnessError(`security.${field} must be an array of non-empty strings`, 'CONFIG_INVALID');
    }
  }
  return config;
}

export async function findRepoRoot(start = process.cwd()) {
  let cursor = path.resolve(start);
  while (true) {
    const candidate = path.join(cursor, '.codex', 'harness.json');
    const config = await readJson(candidate, { required: false });
    if (config) return cursor;
    const parent = path.dirname(cursor);
    if (parent === cursor) throw new HarnessError(`Cannot find .codex/harness.json from ${start}`, 'REPO_ROOT_NOT_FOUND');
    cursor = parent;
  }
}

export async function loadConfig(repoRoot = undefined) {
  const root = repoRoot ? path.resolve(repoRoot) : await findRepoRoot();
  const realRoot = await realpath(root);
  const configPath = path.join(realRoot, '.codex', 'harness.json');
  const config = validateHarnessConfig(await readJson(configPath));
  const resolved = {
    ...config,
    repoRoot: realRoot,
    configPath,
    statePath: path.resolve(realRoot, config.stateDir),
    catalogPath: path.resolve(realRoot, config.catalogFile),
    verificationPath: path.resolve(realRoot, config.verificationFile)
  };
  for (const candidate of [resolved.statePath, resolved.catalogPath, resolved.verificationPath]) {
    if (!isPathInside(realRoot, candidate)) throw new HarnessError(`Configured path escapes repository: ${candidate}`, 'CONFIG_INVALID');
  }
  return resolved;
}
