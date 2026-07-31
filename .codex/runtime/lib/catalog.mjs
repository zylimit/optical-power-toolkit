import path from 'node:path';
import { HarnessError, normalizeRepoPath, readJson, sha256, stableJson, toPosix } from './common.mjs';
import { changedPaths, changedPathsFromBaseline, trackedPaths } from './git.mjs';

function globRegex(pattern) {
  let source = '';
  const value = toPosix(pattern);
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (char === '*') {
      if (value[index + 1] === '*') {
        index += 1;
        source += value[index + 1] === '/' ? '(?:.*/)?' : '.*';
        if (value[index + 1] === '/') index += 1;
      } else {
        source += '[^/]*';
      }
    } else if (char === '?') {
      source += '[^/]';
    } else {
      source += char.replace(/[|\\{}()[\]^$+?.]/g, '\\$&');
    }
  }
  return new RegExp(`^${source}$`);
}

export function matchesGlob(relativePath, pattern) {
  return globRegex(pattern).test(toPosix(relativePath));
}

function moduleMatches(module, relativePath) {
  const target = toPosix(relativePath);
  const root = module.root === '.' ? '' : module.root.replace(/\/$/, '');
  if (root && target !== root && !target.startsWith(`${root}/`)) return false;
  const inside = root ? target.slice(root.length).replace(/^\//, '') : target;
  return module.paths.some((pattern) => matchesGlob(inside, pattern));
}

function validateStringArray(value, label) {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string' || !item)) {
    throw new HarnessError(`${label} must be an array of non-empty strings`, 'CATALOG_INVALID');
  }
}

export function validateCatalog(catalog) {
  if (!catalog || typeof catalog !== 'object' || Array.isArray(catalog)) throw new HarnessError('Catalog must be an object', 'CATALOG_INVALID');
  const allowed = new Set(['version', 'globalPaths', 'ignored', 'modules']);
  for (const key of Object.keys(catalog)) if (!allowed.has(key)) throw new HarnessError(`Unknown catalog field: ${key}`, 'CATALOG_INVALID');
  if (catalog.version !== 1 || !Array.isArray(catalog.modules)) throw new HarnessError('Catalog version/modules are invalid', 'CATALOG_INVALID');
  validateStringArray(catalog.globalPaths ?? [], 'globalPaths');
  for (const pattern of catalog.globalPaths ?? []) {
    if (path.isAbsolute(pattern) || pattern.split(/[\\/]/).includes('..')) throw new HarnessError(`Global pattern escapes repository: ${pattern}`, 'CATALOG_INVALID');
  }
  if (!Array.isArray(catalog.ignored ?? [])) throw new HarnessError('ignored must be an array', 'CATALOG_INVALID');
  for (const entry of catalog.ignored ?? []) {
    if (!entry || typeof entry.pattern !== 'string' || typeof entry.reason !== 'string' || !entry.reason.trim()) {
      throw new HarnessError('Every ignored pattern needs a reason', 'CATALOG_INVALID');
    }
    if (entry.pattern.split(/[\\/]/).includes('..') || path.isAbsolute(entry.pattern)) throw new HarnessError(`Ignored pattern escapes repository: ${entry.pattern}`, 'CATALOG_INVALID');
  }
  const ids = new Set();
  for (const module of catalog.modules) {
    const fields = new Set(['id', 'root', 'paths', 'dependsOn', 'shared', 'owners', 'contracts', 'capsule', 'tests', 'verification']);
    for (const key of Object.keys(module)) if (!fields.has(key)) throw new HarnessError(`Unknown module field ${module.id ?? '?'}: ${key}`, 'CATALOG_INVALID');
    if (!/^[a-z][a-z0-9-]*$/.test(module.id ?? '')) throw new HarnessError(`Invalid module id: ${module.id}`, 'CATALOG_INVALID');
    if (ids.has(module.id)) throw new HarnessError(`Duplicate module id: ${module.id}`, 'CATALOG_INVALID');
    ids.add(module.id);
    module.root = module.root === '.' ? '.' : normalizeRepoPath(module.root);
    for (const label of ['paths', 'dependsOn', 'owners', 'contracts', 'tests', 'verification']) validateStringArray(module[label], `${module.id}.${label}`);
    if (typeof module.shared !== 'boolean') throw new HarnessError(`${module.id}.shared must be boolean`, 'CATALOG_INVALID');
    for (const pattern of module.paths) {
      if (path.isAbsolute(pattern) || pattern.split(/[\\/]/).includes('..')) throw new HarnessError(`Module path escapes root: ${module.id}:${pattern}`, 'CATALOG_INVALID');
    }
    for (const file of [...module.contracts, ...module.tests, ...(module.capsule ? [module.capsule] : [])]) normalizeRepoPath(file);
    if ((module.root === '.' || module.root === '') && module.paths.some((item) => item === '**' || item === '**/*')) {
      throw new HarnessError(`Root catch-all module masks coverage gaps: ${module.id}`, 'CATALOG_ROOT_MASK');
    }
  }
  for (const module of catalog.modules) {
    for (const dependency of module.dependsOn) if (!ids.has(dependency)) throw new HarnessError(`Unknown dependency ${dependency} in ${module.id}`, 'CATALOG_UNKNOWN_DEPENDENCY');
  }
  return catalog;
}

export async function loadCatalog(config) {
  return validateCatalog(await readJson(config.catalogPath));
}

export function classifyPath(catalog, relativePath) {
  const target = normalizeRepoPath(relativePath);
  const ignored = (catalog.ignored ?? []).find((entry) => matchesGlob(target, entry.pattern));
  if (ignored) return { path: target, classification: 'ignored', reason: ignored.reason, modules: [] };
  if ((catalog.globalPaths ?? []).some((pattern) => matchesGlob(target, pattern))) {
    return { path: target, classification: 'global', reason: 'global path', modules: [] };
  }
  const matches = catalog.modules.filter((module) => moduleMatches(module, target));
  if (!matches.length) return { path: target, classification: 'unmapped', reason: 'no module pattern matched', modules: [] };
  matches.sort((left, right) => right.root.length - left.root.length || left.id.localeCompare(right.id));
  const deepestLength = matches[0].root.length;
  const deepest = matches.filter((item) => item.root.length === deepestLength);
  if (deepest.length > 1) {
    return { path: target, classification: 'overlap', reason: 'multiple modules match at the same depth', modules: deepest.map((item) => item.id) };
  }
  return { path: target, classification: 'mapped', reason: 'deepest effective module match', module: deepest[0].id, modules: matches.map((item) => item.id) };
}

export async function lintCatalog(config, explicitPaths = []) {
  const catalog = await loadCatalog(config);
  const tracked = await trackedPaths(config.repoRoot, config.catalog.maxTrackedPaths);
  const paths = [...new Set([...tracked.paths, ...explicitPaths.map(normalizeRepoPath)])].sort();
  const entries = paths.map((item) => classifyPath(catalog, item));
  const counts = entries.reduce((result, item) => ({ ...result, [item.classification]: (result[item.classification] ?? 0) + 1 }), {});
  const failures = entries.filter((item) => item.classification === 'unmapped' || item.classification === 'overlap');
  if (tracked.truncated) failures.push({ path: '<tracked-path-limit>', classification: 'truncated', reason: `${tracked.total} tracked paths exceed limit` });
  return { ok: failures.length === 0, catalogHash: sha256(stableJson(catalog)), total: paths.length, counts, failures, entries, truncated: tracked.truncated };
}

function reverseGraph(catalog) {
  const graph = new Map(catalog.modules.map((module) => [module.id, new Set()]));
  for (const module of catalog.modules) {
    for (const dependency of module.dependsOn) graph.get(dependency).add(module.id);
  }
  return graph;
}

export function reverseDependencyClosure(catalog, directIds) {
  const graph = reverseGraph(catalog);
  const affected = new Set(directIds);
  const queue = [...directIds];
  while (queue.length) {
    const current = queue.shift();
    for (const consumer of graph.get(current) ?? []) {
      if (!affected.has(consumer)) {
        affected.add(consumer);
        queue.push(consumer);
      }
    }
  }
  return [...affected].sort();
}

export async function analyzeImpact(config, options = {}) {
  const catalog = await loadCatalog(config);
  const discovered = options.paths
    ? { paths: options.paths.map(normalizeRepoPath), truncated: Boolean(options.truncated), note: 'explicit paths' }
    : options.baseline
      ? await changedPathsFromBaseline(config.repoRoot, options.baseline)
      : await changedPaths(config.repoRoot);
  const changedLimit = config.catalog.maxChangedPaths;
  const changes = {
    ...discovered,
    paths: discovered.paths.slice(0, changedLimit),
    truncated: Boolean(discovered.truncated || discovered.paths.length > changedLimit),
    total: discovered.paths.length
  };
  const classifications = changes.paths.map((item) => classifyPath(catalog, item));
  const direct = [...new Set(classifications.filter((item) => item.classification === 'mapped').map((item) => item.module))].sort();
  const directModules = catalog.modules.filter((module) => direct.includes(module.id));
  const expansionReasons = [];
  if (changes.isGit === false && !options.paths) expansionReasons.push('Git change discovery unavailable');
  if (changes.truncated) expansionReasons.push('repository map truncated');
  if (classifications.some((item) => item.classification === 'global')) expansionReasons.push('global path changed');
  if (classifications.some((item) => item.classification === 'unmapped' || item.classification === 'overlap')) expansionReasons.push('unmapped or overlapping path');
  if (directModules.some((module) => module.shared)) expansionReasons.push('shared module changed');
  const all = catalog.modules.map((module) => module.id).sort();
  const affectedModules = expansionReasons.length ? all : reverseDependencyClosure(catalog, direct);
  return {
    catalogHash: sha256(stableJson(catalog)),
    changedPaths: changes.paths,
    classifications,
    directModules: direct,
    affectedModules,
    expandedToAll: expansionReasons.length > 0,
    expansionReasons,
    baseline: changes.baseline ?? null,
    truncated: changes.truncated,
    degraded: !changes.isGit && !options.paths ? [changes.note ?? 'Git unavailable'] : []
  };
}

export async function discoverModuleCandidates(config) {
  const tracked = await trackedPaths(config.repoRoot, config.catalog.maxTrackedPaths);
  const manifestNames = new Set(['package.json', 'pyproject.toml', 'Cargo.toml', 'go.mod', 'pom.xml', 'build.gradle']);
  const roots = tracked.paths.filter((item) => manifestNames.has(path.posix.basename(item))).map((item) => path.posix.dirname(item)).sort();
  return { roots: [...new Set(roots)], truncated: tracked.truncated, source: 'tracked manifest paths only' };
}
