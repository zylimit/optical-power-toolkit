import { randomUUID } from 'node:crypto';
import { HarnessError, normalizeRepoPath } from './common.mjs';
import { readState, updateState } from './state.mjs';

const LEASE_FILE = 'leases.json';
const STATUSES = new Set(['active', 'expired', 'released']);

export const LEASE_ADVISORY = 'Path leases are repository-local coordination hints, not OS locks or cross-host enforcement.';

function emptyLedger() {
  return { version: 1, leases: [] };
}

function comparisonPath(value) {
  return process.platform === 'win32' ? value.toLowerCase() : value;
}

function normalizePaths(input) {
  const values = Array.isArray(input)
    ? input
    : typeof input === 'string' ? input.split(',') : null;
  if (!values) throw new HarnessError('Lease paths must be a comma-separated string or array', 'LEASE_INVALID');
  const unique = new Map();
  for (const value of values) {
    if (typeof value !== 'string' || !value.trim()) throw new HarnessError('Lease paths must contain non-empty strings', 'LEASE_INVALID');
    const normalized = normalizeRepoPath(value.trim());
    const key = comparisonPath(normalized);
    if (!unique.has(key)) unique.set(key, normalized);
  }
  if (!unique.size) throw new HarnessError('Lease paths must not be empty', 'LEASE_INVALID');
  return [...unique.values()].sort((left, right) => comparisonPath(left).localeCompare(comparisonPath(right)));
}

function requiredString(value, label) {
  if (typeof value !== 'string' || !value.trim() || value.length > 200) {
    throw new HarnessError(`Lease ${label} must be a non-empty string of at most 200 characters`, 'LEASE_INVALID');
  }
  return value.trim();
}

function optionalString(value, label, maxLength = 1000) {
  if (value === undefined || value === null || value === '') return null;
  if (typeof value !== 'string' || !value.trim() || value.length > maxLength) {
    throw new HarnessError(`Lease ${label} must be a non-empty string of at most ${maxLength} characters`, 'LEASE_INVALID');
  }
  return value.trim();
}

function taskId(value) {
  const normalized = optionalString(value, 'taskId', 80);
  if (normalized && !/^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$/.test(normalized)) {
    throw new HarnessError('Lease taskId has an invalid format', 'LEASE_INVALID');
  }
  return normalized;
}

function leaseHours(value) {
  if ((typeof value !== 'number' && typeof value !== 'string') || value === '') {
    throw new HarnessError('Lease hours must be between 1 and 720', 'LEASE_INVALID');
  }
  const hours = Number(value);
  if (!Number.isFinite(hours) || hours < 1 || hours > 720) {
    throw new HarnessError('Lease hours must be between 1 and 720', 'LEASE_INVALID');
  }
  return hours;
}

function validateLedger(ledger) {
  if (!ledger || ledger.version !== 1 || !Array.isArray(ledger.leases)) throw new HarnessError('Invalid lease ledger', 'STATE_CORRUPT');
  for (const lease of ledger.leases) {
    let normalizedPaths;
    try { normalizedPaths = normalizePaths(lease?.paths); } catch { throw new HarnessError('Invalid lease record', 'STATE_CORRUPT'); }
    const validTaskId = lease.taskId === null || (typeof lease.taskId === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$/.test(lease.taskId));
    const validWorktree = lease.worktree === null || (typeof lease.worktree === 'string' && lease.worktree.length > 0);
    const validIntegrationOwner = lease.integrationOwner === null || (typeof lease.integrationOwner === 'string' && lease.integrationOwner.length > 0);
    if (!lease || typeof lease.id !== 'string' || !lease.id || !pathsEqual(lease.paths, normalizedPaths)
      || typeof lease.owner !== 'string' || !lease.owner || !validTaskId || !validWorktree || !validIntegrationOwner || !STATUSES.has(lease.status)
      || !Number.isFinite(Date.parse(lease.acquiredAt)) || !Number.isFinite(Date.parse(lease.expiresAt))
      || (lease.releasedAt !== null && !Number.isFinite(Date.parse(lease.releasedAt)))
      || (lease.status === 'released' && lease.releasedAt === null)) {
      throw new HarnessError('Invalid lease record', 'STATE_CORRUPT');
    }
  }
  return ledger;
}

function expireLeases(ledger, now) {
  return {
    ...ledger,
    leases: ledger.leases.map((lease) => lease.status === 'active' && Date.parse(lease.expiresAt) <= now
      ? { ...lease, status: 'expired' }
      : lease)
  };
}

function pathsEqual(left, right) {
  if (left.length !== right.length) return false;
  const leftKeys = left.map(comparisonPath).sort();
  const rightKeys = right.map(comparisonPath).sort();
  return leftKeys.every((value, index) => value === rightKeys[index]);
}

export function leasePathsOverlap(left, right) {
  return left.some((leftPath) => right.some((rightPath) => {
    const leftKey = comparisonPath(leftPath);
    const rightKey = comparisonPath(rightPath);
    return leftKey === rightKey || leftKey.startsWith(`${rightKey}/`) || rightKey.startsWith(`${leftKey}/`);
  }));
}

function withAdvisory(lease) {
  return { ...lease, advisory: LEASE_ADVISORY };
}

export async function acquireLease(config, input) {
  const paths = normalizePaths(input?.paths);
  const owner = requiredString(input?.owner, 'owner');
  const normalizedTaskId = taskId(input?.taskId);
  const worktree = optionalString(input?.worktree, 'worktree');
  const integrationOwner = optionalString(input?.integrationOwner, 'integrationOwner', 200);
  const hours = leaseHours(input?.hours ?? 24);
  const now = Date.now();
  let acquired;
  await updateState(config, LEASE_FILE, emptyLedger(), (state) => {
    const ledger = expireLeases(validateLedger(state), now);
    const idempotent = ledger.leases.find((lease) => lease.status === 'active' && lease.owner === owner
      && lease.taskId === normalizedTaskId && pathsEqual(lease.paths, paths));
    if (idempotent) {
      acquired = idempotent;
      return ledger;
    }
    const conflicts = ledger.leases.filter((lease) => lease.status === 'active' && leasePathsOverlap(lease.paths, paths));
    if (conflicts.length) {
      throw new HarnessError('Requested lease paths overlap an active lease', 'LEASE_CONFLICT', {
        requestedPaths: paths,
        conflicts: conflicts.map((lease) => ({ id: lease.id, paths: lease.paths, owner: lease.owner, taskId: lease.taskId, expiresAt: lease.expiresAt }))
      });
    }
    const acquiredAt = new Date(now).toISOString();
    acquired = {
      id: randomUUID(),
      paths,
      owner,
      taskId: normalizedTaskId,
      worktree,
      integrationOwner,
      acquiredAt,
      expiresAt: new Date(now + hours * 3600000).toISOString(),
      releasedAt: null,
      status: 'active'
    };
    return { ...ledger, leases: [...ledger.leases, acquired] };
  });
  return withAdvisory(acquired);
}

export async function leaseStatus(config, filters = {}) {
  const owner = optionalString(filters.owner, 'owner', 200);
  const normalizedTaskId = taskId(filters.taskId);
  const now = Date.now();
  let leases;
  await updateState(config, LEASE_FILE, emptyLedger(), (state) => {
    const ledger = expireLeases(validateLedger(state), now);
    leases = ledger.leases.filter((lease) => (!owner || lease.owner === owner) && (!normalizedTaskId || lease.taskId === normalizedTaskId));
    return ledger;
  });
  return { version: 1, advisory: LEASE_ADVISORY, leases };
}

export async function releaseLease(config, input) {
  const id = requiredString(input?.id, 'id');
  const owner = requiredString(input?.owner, 'owner');
  const now = Date.now();
  let released;
  await updateState(config, LEASE_FILE, emptyLedger(), (state) => {
    const ledger = expireLeases(validateLedger(state), now);
    const index = ledger.leases.findIndex((lease) => lease.id === id);
    if (index < 0) throw new HarnessError(`Lease not found: ${id}`, 'LEASE_NOT_FOUND');
    const lease = ledger.leases[index];
    if (lease.owner !== owner) throw new HarnessError('Only the lease owner can release it', 'LEASE_OWNER_MISMATCH');
    released = lease.status === 'released'
      ? lease
      : { ...lease, status: 'released', releasedAt: new Date(now).toISOString() };
    const leases = [...ledger.leases];
    leases[index] = released;
    return { ...ledger, leases };
  });
  return withAdvisory(released);
}
