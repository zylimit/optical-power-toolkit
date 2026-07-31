import { lstat, readFile, realpath } from 'node:fs/promises';
import path from 'node:path';
import { atomicWrite, boundedText, contentHash, HarnessError, isPathInside, normalizeRepoPath, sha256, stableJson } from './common.mjs';
import { loadCatalog, analyzeImpact } from './catalog.mjs';
import { gitFingerprint } from './git.mjs';
import { getTask } from './tasks.mjs';
import { stateFile } from './state.mjs';

function denied(config, relativePath) {
  const target = normalizeRepoPath(relativePath);
  const pieces = target.toLowerCase().split('/');
  const base = pieces.at(-1);
  if (pieces.includes('.git')) return 'Git metadata';
  if (target.startsWith('.codex/harness-state/')) return 'runtime state';
  if (config.security.dependencyDirs.some((item) => pieces.includes(item.toLowerCase()))) return 'dependency or generated directory';
  if (config.security.allowedSecretTemplates.map((item) => item.toLowerCase()).includes(base)) return null;
  if (config.security.secretNames.map((item) => item.toLowerCase()).includes(base)) return 'secret file';
  if (base.startsWith('.env')) return 'secret environment file';
  if (config.security.secretExtensions.some((item) => base.endsWith(item.toLowerCase()))) return 'secret extension';
  return null;
}

function samePhysicalPath(left, right) {
  const normalize = (value) => process.platform === 'win32' ? path.resolve(value).toLowerCase() : path.resolve(value);
  return normalize(left) === normalize(right);
}

async function regularContextFile(config, relativePath) {
  const target = normalizeRepoPath(relativePath);
  const pieces = target.split('/');
  const realRoot = await realpath(config.repoRoot);
  let current = realRoot;
  for (let index = 0; index < pieces.length; index += 1) {
    current = path.join(current, pieces[index]);
    const safePath = pieces.slice(0, index + 1).join('/');
    let info;
    try {
      info = await lstat(current);
    } catch (error) {
      if (error.code === 'ENOENT') return { path: target, omitted: 'missing file' };
      throw error;
    }
    if (info.isSymbolicLink()) return { path: safePath, omitted: 'symbolic link or junction' };
    if (index < pieces.length - 1 && !info.isDirectory()) return { path: safePath, omitted: 'special path ancestor' };
    if (index === pieces.length - 1 && !info.isFile()) return { path: safePath, omitted: 'special file type' };
    let physical;
    try {
      physical = await realpath(current);
    } catch (error) {
      if (error.code === 'ENOENT') return { path: safePath, omitted: 'missing file' };
      throw error;
    }
    if (!isPathInside(realRoot, physical) || !samePhysicalPath(current, physical)) {
      return { path: safePath, omitted: 'symbolic link, junction, or path escape' };
    }
  }
  return { path: target, physical: current };
}

async function readBounded(config, relativePath, maxChars) {
  const reason = denied(config, relativePath);
  const safeFile = await regularContextFile(config, relativePath);
  if (safeFile.omitted && /symbolic link|junction|path escape|special/.test(safeFile.omitted)) return safeFile;
  if (reason) return { path: safeFile.path, omitted: reason };
  if (safeFile.omitted) return safeFile;
  if (maxChars <= 0) return { path: safeFile.path, omitted: 'total character budget' };
  try {
    const buffer = await readFile(safeFile.physical);
    if (buffer.includes(0)) return { path: safeFile.path, omitted: 'binary file' };
    const text = buffer.toString('utf8');
    const content = boundedText(text, maxChars).slice(0, maxChars);
    return { path: safeFile.path, content, truncated: text.length > maxChars };
  } catch (error) {
    if (error.code === 'ENOENT') return { path: safeFile.path, omitted: 'missing file' };
    throw error;
  }
}

function sanitizedImpact(impact, pathDisplays) {
  const display = (value) => pathDisplays.get(value) ?? value;
  const changedPaths = [...new Set(impact.changedPaths.map(display))];
  const classifications = [];
  const seen = new Set();
  for (const item of impact.classifications) {
    const sanitized = { ...item, path: display(item.path) };
    const key = stableJson(sanitized);
    if (!seen.has(key)) { seen.add(key); classifications.push(sanitized); }
  }
  return { ...impact, changedPaths, classifications };
}

function contentChars(packFields) {
  return packFields.canonicalDiff.length + packFields.included.reduce((total, item) => total + item.content.length, 0);
}

function serializePack(packFields, limits) {
  let serializedChars = 0;
  let serializedBytes = 0;
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const budget = {
      ...limits,
      contentChars: contentChars(packFields),
      serializedChars,
      serializedBytes,
      usedChars: serializedChars
    };
    const packBase = { ...packFields, budget };
    const packHash = sha256(stableJson(packBase));
    const pack = { ...packBase, packHash, contentHash: contentHash({ ...packBase, packHash }) };
    const body = `${JSON.stringify(pack, null, 2)}\n`;
    const nextChars = body.length;
    const nextBytes = Buffer.byteLength(body, 'utf8');
    if (nextChars === serializedChars && nextBytes === serializedBytes) return { pack, body };
    serializedChars = nextChars;
    serializedBytes = nextBytes;
  }
  throw new HarnessError('Context pack budget did not stabilize', 'CONTEXT_BUDGET_FAILED');
}

function fitsBudget(serialized, totalChars) {
  return serialized.pack.budget.serializedChars <= totalChars && serialized.pack.budget.serializedBytes <= totalChars;
}

function overBudgetBy(serialized, totalChars) {
  return Math.max(serialized.pack.budget.serializedChars - totalChars, serialized.pack.budget.serializedBytes - totalChars, 0);
}

function addTruncationReason(packFields, reason) {
  if (!packFields.truncation.reasons.includes(reason)) packFields.truncation.reasons.push(reason);
}

function fitContextPack(packFields, limits) {
  const working = structuredClone(packFields);
  let serialized = serializePack(working, limits);
  if (fitsBudget(serialized, limits.totalChars)) return serialized;

  if (working.impact.classifications.length) {
    working.truncation.impactClassificationsDropped += working.impact.classifications.length;
    working.impact.classifications = [];
    addTruncationReason(working, 'impact classifications trimmed to total budget');
    serialized = serializePack(working, limits);
  }

  if (!fitsBudget(serialized, limits.totalChars) && working.omitted.length) {
    working.truncation.omittedEntriesDropped += working.omitted.length;
    working.omitted = [];
    addTruncationReason(working, 'omitted detail trimmed to total budget');
    serialized = serializePack(working, limits);
  }

  while (!fitsBudget(serialized, limits.totalChars)) {
    const item = [...working.included].sort((left, right) => right.content.length - left.content.length)[0];
    if (!item?.content.length) break;
    const drop = Math.min(item.content.length, Math.max(1, overBudgetBy(serialized, limits.totalChars) + 64));
    item.content = item.content.slice(0, item.content.length - drop);
    item.truncated = true;
    working.truncation.includedContentCharsDropped += drop;
    addTruncationReason(working, 'included content trimmed to total budget');
    serialized = serializePack(working, limits);
  }

  while (!fitsBudget(serialized, limits.totalChars) && working.included.length) {
    working.included.pop();
    working.truncation.includedEntriesDropped += 1;
    addTruncationReason(working, 'included entries trimmed to total budget');
    serialized = serializePack(working, limits);
  }

  while (!fitsBudget(serialized, limits.totalChars) && working.canonicalDiff.length) {
    const drop = Math.min(working.canonicalDiff.length, Math.max(1, overBudgetBy(serialized, limits.totalChars) + 64));
    working.canonicalDiff = working.canonicalDiff.slice(0, working.canonicalDiff.length - drop);
    working.truncation.canonicalDiffCharsDropped += drop;
    addTruncationReason(working, 'canonical diff trimmed to total budget');
    serialized = serializePack(working, limits);
  }

  for (const key of ['changedPaths', 'degraded', 'expansionReasons', 'directModules', 'affectedModules']) {
    if (fitsBudget(serialized, limits.totalChars)) break;
    if (!working.impact[key].length) continue;
    working.truncation.impactDetailsDropped += working.impact[key].length;
    working.impact[key] = [];
    addTruncationReason(working, 'impact detail trimmed to total budget');
    serialized = serializePack(working, limits);
  }

  for (const key of ['ownedPaths', 'specRefs', 'planRefs']) {
    if (fitsBudget(serialized, limits.totalChars)) break;
    if (!working.task[key].length) continue;
    working.truncation.taskDetailsDropped += working.task[key].length;
    working.task[key] = [];
    addTruncationReason(working, 'task detail trimmed to total budget');
    serialized = serializePack(working, limits);
  }

  for (const key of ['goal', 'scope', 'outOfScope']) {
    if (fitsBudget(serialized, limits.totalChars)) break;
    if (!working.task[key]) continue;
    working.truncation.taskDetailsDropped += working.task[key].length;
    working.task[key] = '';
    addTruncationReason(working, 'task detail trimmed to total budget');
    serialized = serializePack(working, limits);
  }

  if (!fitsBudget(serialized, limits.totalChars)) {
    throw new HarnessError(`Context totalChars (${limits.totalChars}) cannot hold the minimum safe envelope`, 'CONTEXT_BUDGET_TOO_SMALL', {
      totalChars: limits.totalChars,
      serializedChars: serialized.pack.budget.serializedChars,
      serializedBytes: serialized.pack.budget.serializedBytes
    });
  }
  return serialized;
}

function contextItemMetadata(item) {
  const { content, ...metadata } = item;
  return content === undefined ? metadata : { ...metadata, contentChars: content.length };
}

function serializedOutputChars(value) {
  return `${JSON.stringify(value, null, 2)}\n`.length;
}

function modelSummary(config, pack, evidencePath, evidenceBytes, evidenceHash) {
  const summary = {
    version: 1,
    kind: 'context-pack-summary',
    createdAt: pack.createdAt,
    summary: `Context pack ${pack.packHash.slice(0, 16)} contains ${pack.included.length} included and ${pack.omitted.length} omitted item(s); full content is stored in evidence.`,
    taskId: pack.task.id,
    packHash: pack.packHash,
    contentHash: pack.contentHash,
    evidencePath,
    evidenceBytes,
    evidenceHash,
    fingerprint: pack.fingerprint,
    impact: {
      directModules: [...pack.impact.directModules],
      affectedModules: [...pack.impact.affectedModules],
      expandedToAll: pack.impact.expandedToAll,
      expansionReasons: [...pack.impact.expansionReasons],
      truncated: pack.impact.truncated,
      degraded: [...pack.impact.degraded]
    },
    included: pack.included.map(contextItemMetadata),
    omitted: pack.omitted.map(contextItemMetadata),
    truncation: pack.truncation,
    metadata: {
      includedTotal: pack.included.length,
      omittedTotal: pack.omitted.length,
      includedDropped: 0,
      omittedDropped: 0,
      impactDropped: 0
    },
    budget: pack.budget
  };
  const limit = config.outputLimits.modelChars;
  const removable = [
    [summary.included, 'includedDropped'],
    [summary.omitted, 'omittedDropped'],
    [summary.impact.degraded, 'impactDropped'],
    [summary.impact.expansionReasons, 'impactDropped'],
    [summary.impact.affectedModules, 'impactDropped'],
    [summary.impact.directModules, 'impactDropped']
  ];
  while (serializedOutputChars(summary) > limit) {
    const target = removable.filter(([items]) => items.length).sort((left, right) => right[0].length - left[0].length)[0];
    if (!target) break;
    target[0].pop();
    summary.metadata[target[1]] += 1;
  }
  if (serializedOutputChars(summary) > limit) {
    throw new HarnessError(`Context pack summary exceeds outputLimits.modelChars (${limit})`, 'CONTEXT_MODEL_LIMIT');
  }
  return summary;
}

export async function buildContextPack(config, options = {}) {
  const task = await getTask(config, options.taskId);
  if (!task) throw new HarnessError('Context pack requires a task', 'TASK_NOT_FOUND');
  const [catalog, impact, fingerprint] = await Promise.all([
    loadCatalog(config),
    analyzeImpact(config, options.paths ? { paths: options.paths } : {}),
    gitFingerprint(config.repoRoot)
  ]);
  const limits = { ...config.context, ...(options.limits ?? {}) };
  const pathDisplays = new Map();
  for (const relative of new Set([...fingerprint.changedPaths, ...impact.changedPaths])) {
    const inspected = await regularContextFile(config, relative);
    pathDisplays.set(relative, inspected.path);
  }
  const safeImpact = sanitizedImpact(impact, pathDisplays);
  const selectedIds = new Set(safeImpact.affectedModules);
  for (const module of catalog.modules.filter((item) => selectedIds.has(item.id))) {
    for (const dependency of module.dependsOn) selectedIds.add(dependency);
  }
  const modules = catalog.modules.filter((module) => selectedIds.has(module.id));
  const priority = [];
  for (const module of modules) {
    if (module.capsule) priority.push({ type: 'capsule', path: module.capsule, module: module.id });
    for (const contract of module.contracts) priority.push({ type: 'contract', path: contract, module: module.id });
  }
  for (const relative of fingerprint.changedPaths) priority.push({ type: 'changed-file', path: relative });
  for (const module of modules) for (const test of module.tests) priority.push({ type: 'test', path: test, module: module.id });
  const unique = [];
  const seen = new Set();
  for (const item of priority) {
    const key = `${item.type}:${item.path}`;
    if (!seen.has(key)) { seen.add(key); unique.push(item); }
  }
  const deniedChanged = fingerprint.changedPaths.filter((item) => denied(config, item)).map((item) => pathDisplays.get(item) ?? item);
  const diffLimit = Math.min(limits.diffChars, limits.totalChars);
  const diffText = deniedChanged.length
    ? boundedText(`[canonical diff omitted because denied paths changed: ${deniedChanged.join(', ')}; binding diff hash: ${fingerprint.diffHash}]`, diffLimit).slice(0, diffLimit)
    : boundedText(Buffer.from(fingerprint.canonical, 'base64').toString('utf8'), diffLimit).slice(0, diffLimit);
  let selectedContentChars = diffText.length;
  const included = [];
  const omitted = [];
  for (const item of unique.slice(0, limits.maxFiles)) {
    const file = await readBounded(config, item.path, Math.min(limits.fileChars, Math.max(0, limits.totalChars - selectedContentChars)));
    if (file.omitted) {
      omitted.push({ ...item, path: file.path, reason: file.omitted });
      continue;
    }
    if (selectedContentChars + file.content.length > limits.totalChars) {
      omitted.push({ ...item, path: file.path, reason: 'total character budget' });
      continue;
    }
    included.push({ ...item, path: file.path, content: file.content, truncated: file.truncated });
    selectedContentChars += file.content.length;
  }
  for (const item of unique.slice(limits.maxFiles)) omitted.push({ ...item, reason: 'file count budget' });
  const packFields = {
    version: 1,
    kind: 'context-pack',
    createdAt: new Date().toISOString(),
    task: {
      id: task.id,
      goal: task.goal,
      scope: task.scope,
      outOfScope: task.outOfScope,
      risk: task.risk,
      ownedPaths: task.ownedPaths,
      specRefs: task.specRefs,
      planRefs: task.planRefs
    },
    fingerprint: { baseCommit: fingerprint.baseCommit, fingerprint: fingerprint.fingerprint, diffHash: fingerprint.diffHash },
    impact: safeImpact,
    canonicalDiff: diffText,
    included,
    omitted,
    truncation: {
      reasons: [],
      impactClassificationsDropped: 0,
      omittedEntriesDropped: 0,
      includedContentCharsDropped: 0,
      includedEntriesDropped: 0,
      canonicalDiffCharsDropped: 0,
      impactDetailsDropped: 0,
      taskDetailsDropped: 0
    }
  };
  const { pack, body } = fitContextPack(packFields, limits);
  if (body.length > limits.totalChars || Buffer.byteLength(body, 'utf8') > limits.totalChars) {
    throw new HarnessError('Context evidence exceeds totalChars before write', 'CONTEXT_BUDGET_FAILED');
  }
  const outputPath = stateFile(config, path.join('context', `${task.id}-${pack.packHash.slice(0, 16)}.json`));
  await atomicWrite(outputPath, body);
  const evidence = await readFile(outputPath);
  const evidencePath = path.relative(config.repoRoot, outputPath).split(path.sep).join('/');
  return modelSummary(config, pack, evidencePath, evidence.length, sha256(evidence));
}
