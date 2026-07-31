import path from 'node:path';
import { fileDigest, HarnessError, normalizeRepoPath } from './common.mjs';
import { gitFingerprint, gitInfo, pathStatus, trackedPaths } from './git.mjs';
import { readState, updateState } from './state.mjs';

const TASK_FILE = 'tasks.json';
const RISKS = new Set(['low', 'medium', 'high']);

function emptyTasks() {
  return { version: 1, activeTaskId: null, tasks: {} };
}

function validateEnvelope(input) {
  for (const field of ['id', 'goal']) {
    if (typeof input[field] !== 'string' || !input[field].trim()) throw new HarnessError(`Task ${field} is required`, 'TASK_INVALID');
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$/.test(input.id)) throw new HarnessError('Task id has an invalid format', 'TASK_INVALID');
  if (!RISKS.has(input.risk)) throw new HarnessError('Task risk must be low, medium, or high', 'TASK_INVALID');
  if (!Array.isArray(input.ownedPaths) || input.ownedPaths.length === 0) throw new HarnessError('Task ownedPaths must not be empty', 'TASK_INVALID');
  return {
    id: input.id,
    goal: input.goal.trim(),
    scope: String(input.scope ?? ''),
    outOfScope: String(input.outOfScope ?? ''),
    risk: input.risk,
    ownedPaths: [...new Set(input.ownedPaths.map(normalizeRepoPath))].sort(),
    specRefs: Array.isArray(input.specRefs) ? input.specRefs.map(String) : [],
    planRefs: Array.isArray(input.planRefs) ? input.planRefs.map(String) : [],
    reviewExclusions: Array.isArray(input.reviewExclusions) ? input.reviewExclusions.map(normalizeRepoPath).sort() : [],
    requiresReview: input.requiresReview !== false
  };
}

async function hashesForPaths(repoRoot, paths) {
  const entries = await Promise.all(paths.map(async (relative) => [relative, await fileDigest(path.join(repoRoot, relative))]));
  return Object.fromEntries(entries);
}

function envelopeOwns(envelope, relativePath) {
  return envelope.ownedPaths.some((owned) => relativePath === owned || relativePath.startsWith(`${owned.replace(/\/$/, '')}/`));
}

async function baselineHashes(repoRoot, envelope, dirty) {
  const tracked = await trackedPaths(repoRoot);
  const candidates = [...new Set([...envelope.ownedPaths, ...tracked.paths, ...dirty.paths])]
    .filter((item) => envelopeOwns(envelope, item));
  return hashesForPaths(repoRoot, candidates);
}

export async function startTask(config, input) {
  const envelope = validateEnvelope(input);
  const [fingerprint, dirty] = await Promise.all([
    gitFingerprint(config.repoRoot),
    pathStatus(config.repoRoot)
  ]);
  const knownHashes = await baselineHashes(config.repoRoot, envelope, dirty);
  const now = new Date().toISOString();
  const task = {
    ...envelope,
    status: 'active',
    createdAt: now,
    updatedAt: now,
    completedAt: null,
    cancelledAt: null,
    baseline: {
      baseCommit: fingerprint.baseCommit,
      fingerprint: fingerprint.fingerprint,
      diffHash: fingerprint.diffHash,
      preExistingDirty: dirty.map,
      knownHashes
    },
    touchedPaths: [],
    lastFingerprint: fingerprint.fingerprint
  };
  await updateState(config, TASK_FILE, emptyTasks(), (state) => {
    if (state.version !== 1 || !state.tasks || typeof state.tasks !== 'object') throw new HarnessError('Invalid task state', 'STATE_CORRUPT');
    if (state.activeTaskId) throw new HarnessError(`Active task already exists: ${state.activeTaskId}`, 'TASK_ACTIVE_EXISTS');
    if (state.tasks[task.id]) throw new HarnessError(`Task already exists: ${task.id}`, 'TASK_EXISTS');
    return { ...state, activeTaskId: task.id, tasks: { ...state.tasks, [task.id]: task } };
  });
  return task;
}

export async function taskState(config) {
  const state = await readState(config, TASK_FILE, emptyTasks());
  if (state.version !== 1 || !state.tasks || typeof state.tasks !== 'object') throw new HarnessError('Invalid task state', 'STATE_CORRUPT');
  return state;
}

export async function getTask(config, id = undefined) {
  const state = await taskState(config);
  const taskId = id ?? state.activeTaskId;
  return taskId ? state.tasks[taskId] ?? null : null;
}

export function pathOwnedByTask(task, relativePath) {
  const target = normalizeRepoPath(relativePath);
  return task.ownedPaths.some((owned) => target === owned || target.startsWith(`${owned.replace(/\/$/, '')}/`));
}

export async function assertTaskBaseline(config, task) {
  const current = await gitInfo(config.repoRoot);
  if (current.baseCommit !== task.baseline.baseCommit) {
    throw new HarnessError(
      `Active task baseline moved from ${task.baseline.baseCommit} to ${current.baseCommit}; cancel or restart the task before continuing`,
      'TASK_BASELINE_MOVED',
      { taskId: task.id, expected: task.baseline.baseCommit, current: current.baseCommit, degraded: !current.isGit }
    );
  }
  return current;
}

export async function preflightTaskWrites(config, paths) {
  const task = await getTask(config);
  if (!task) return { ok: true, task: null };
  await assertTaskBaseline(config, task);
  const relevant = paths.map(normalizeRepoPath).filter((item) => pathOwnedByTask(task, item));
  const conflicts = [];
  for (const relative of relevant) {
    const known = Object.hasOwn(task.baseline.knownHashes, relative) ? task.baseline.knownHashes[relative] : null;
    const current = await fileDigest(path.join(config.repoRoot, relative));
    if (known !== current) conflicts.push({ path: relative, known, current });
  }
  if (conflicts.length) throw new HarnessError('Owned paths changed outside the active task; coordinate before writing', 'TASK_CONCURRENT_CHANGE', conflicts);
  return { ok: true, task, paths: relevant };
}

export async function refreshTask(config, explicitPaths = []) {
  let updated;
  await updateState(config, TASK_FILE, emptyTasks(), async (state) => {
    const task = state.activeTaskId ? state.tasks[state.activeTaskId] : null;
    if (!task) return state;
    if (!task || task.status !== 'active') throw new HarnessError('Active task changed while refreshing', 'TASK_STATE_CHANGED');
    await assertTaskBaseline(config, task);
    const refreshedPaths = [...new Set(explicitPaths.map(normalizeRepoPath).filter((item) => pathOwnedByTask(task, item)))].sort();
    const touched = [...new Set([...task.touchedPaths, ...refreshedPaths])].sort();
    const knownHashes = { ...task.baseline.knownHashes, ...(await hashesForPaths(config.repoRoot, refreshedPaths)) };
    const fingerprint = await gitFingerprint(config.repoRoot);
    updated = {
      ...task,
      touchedPaths: touched,
      lastFingerprint: fingerprint.fingerprint,
      updatedAt: new Date().toISOString(),
      baseline: { ...task.baseline, knownHashes }
    };
    return { ...state, tasks: { ...state.tasks, [task.id]: updated } };
  });
  return updated;
}

export async function cancelTask(config, reason = '') {
  let cancelled;
  await updateState(config, TASK_FILE, emptyTasks(), (state) => {
    if (!state.activeTaskId) throw new HarnessError('No active task', 'TASK_NOT_ACTIVE');
    const task = state.tasks[state.activeTaskId];
    cancelled = { ...task, status: 'cancelled', cancelReason: String(reason), cancelledAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
    return { ...state, activeTaskId: null, tasks: { ...state.tasks, [task.id]: cancelled } };
  });
  return cancelled;
}

export async function markTaskComplete(config, taskId, completion) {
  let completed;
  await updateState(config, TASK_FILE, emptyTasks(), (state) => {
    if (state.activeTaskId !== taskId) throw new HarnessError(`Task is not active: ${taskId}`, 'TASK_NOT_ACTIVE');
    const task = state.tasks[taskId];
    completed = { ...task, status: 'completed', completion, completedAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
    return { ...state, activeTaskId: null, tasks: { ...state.tasks, [taskId]: completed } };
  });
  return completed;
}
