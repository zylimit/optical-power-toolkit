import { readFile, realpath } from 'node:fs/promises';
import path from 'node:path';
import { contentHash, HarnessError, isPathInside, sha256 } from './common.mjs';
import { appendLedger, readState } from './state.mjs';

const LEDGER_FILE = 'quality-ledger.json';

function validateIntegrity(record, kind) {
  if (!record || record.version !== 1 || record.kind !== kind) throw new HarnessError(`Invalid ${kind} record`, 'RECORD_INVALID');
  if (record.contentHash !== contentHash(record)) throw new HarnessError(`${kind} content hash mismatch`, 'RECORD_TAMPERED');
  return record;
}

function verificationEvidencePath(config, record) {
  if (typeof record.evidencePath !== 'string' || !record.evidencePath
    || path.isAbsolute(record.evidencePath) || record.evidencePath.split(/[\\/]/).includes('..')) {
    throw new HarnessError('Verification evidence path is unsafe', 'EVIDENCE_PATH_UNSAFE');
  }
  const absolute = path.resolve(config.repoRoot, record.evidencePath);
  const evidenceRoot = path.join(config.statePath, 'evidence');
  if (!isPathInside(evidenceRoot, absolute)) throw new HarnessError('Verification evidence must stay in harness-state/evidence', 'EVIDENCE_PATH_UNSAFE');
  return absolute;
}

async function validateVerification(config, record) {
  validateIntegrity(record, 'verification');
  if (!Number.isInteger(record.evidenceBytes) || record.evidenceBytes < 0
    || typeof record.evidenceHash !== 'string' || !/^[a-f0-9]{64}$/.test(record.evidenceHash)) {
    throw new HarnessError('Verification receipt lacks valid evidence integrity metadata', 'RECORD_INVALID');
  }
  const evidencePath = verificationEvidencePath(config, record);
  let resolvedState;
  let resolvedEvidence;
  try {
    [resolvedState, resolvedEvidence] = await Promise.all([realpath(config.statePath), realpath(evidencePath)]);
  } catch (error) {
    if (error.code === 'ENOENT') throw new HarnessError(`Verification evidence is missing: ${record.evidencePath}`, 'EVIDENCE_MISSING');
    throw error;
  }
  if (!isPathInside(path.join(resolvedState, 'evidence'), resolvedEvidence)) {
    throw new HarnessError('Verification evidence resolves outside harness-state/evidence', 'EVIDENCE_PATH_UNSAFE');
  }
  const evidence = await readFile(resolvedEvidence);
  if (evidence.length !== record.evidenceBytes) throw new HarnessError('Verification evidence byte length mismatch', 'EVIDENCE_TAMPERED');
  if (sha256(evidence) !== record.evidenceHash) throw new HarnessError('Verification evidence hash mismatch', 'EVIDENCE_TAMPERED');
  return record;
}

export async function qualityLedger(config) {
  const ledger = await readState(config, LEDGER_FILE, { version: 1, entries: [] });
  if (ledger.version !== 1 || !Array.isArray(ledger.entries)) throw new HarnessError('Invalid quality ledger', 'STATE_CORRUPT');
  return ledger;
}

export async function recordVerification(config, receipt) {
  await validateVerification(config, receipt);
  await appendLedger(config, LEDGER_FILE, receipt);
  return receipt;
}

export function createReviewReceipt(input) {
  for (const field of ['taskId', 'baseCommit', 'diffHash', 'reviewer', 'decision']) {
    if (typeof input[field] !== 'string' || !input[field]) throw new HarnessError(`Review ${field} is required`, 'REVIEW_INVALID');
  }
  if (!Array.isArray(input.scope) || !Array.isArray(input.exclusions) || !Array.isArray(input.findings) || !Array.isArray(input.notReviewed)) {
    throw new HarnessError('Review scope/exclusions/findings/notReviewed must be arrays', 'REVIEW_INVALID');
  }
  const review = {
    version: 1,
    kind: 'review',
    taskId: input.taskId,
    baseCommit: input.baseCommit,
    diffHash: input.diffHash,
    scope: input.scope.map(String).sort(),
    exclusions: input.exclusions.map(String).sort(),
    reviewer: input.reviewer,
    decision: input.decision,
    findings: input.findings,
    notReviewed: input.notReviewed,
    createdAt: input.createdAt ?? new Date().toISOString()
  };
  return { ...review, contentHash: contentHash(review) };
}

export async function recordReview(config, input) {
  const review = input.contentHash ? validateIntegrity(input, 'review') : createReviewReceipt(input);
  await appendLedger(config, LEDGER_FILE, review);
  return review;
}

export function createWaiver(input, check) {
  if (check.class === 'security') throw new HarnessError('Security checks cannot be waived', 'WAIVER_FORBIDDEN');
  for (const field of ['taskId', 'checkId', 'fingerprint', 'approver', 'reason', 'expiresAt', 'compensation', 'followUp']) {
    if (typeof input[field] !== 'string' || !input[field]) throw new HarnessError(`Waiver ${field} is required`, 'WAIVER_INVALID');
  }
  if (typeof input.approvalEvidence !== 'string' || !input.approvalEvidence.trim()) {
    throw new HarnessError('Waiver approvalEvidence is required; a local waiver is not identity authentication', 'WAIVER_INVALID');
  }
  const expires = Date.parse(input.expiresAt);
  if (!Number.isFinite(expires) || expires <= Date.now()) throw new HarnessError('Waiver expiry must be in the future', 'WAIVER_INVALID');
  const waiver = {
    version: 1,
    kind: 'waiver',
    taskId: input.taskId,
    checkId: input.checkId,
    fingerprint: input.fingerprint,
    approver: input.approver,
    reason: input.reason,
    expiresAt: new Date(expires).toISOString(),
    compensation: input.compensation,
    followUp: input.followUp,
    approvalEvidence: input.approvalEvidence,
    createdAt: input.createdAt ?? new Date().toISOString()
  };
  return { ...waiver, contentHash: contentHash(waiver) };
}

export async function recordWaiver(config, input, check) {
  const waiver = input.contentHash ? validateIntegrity(input, 'waiver') : createWaiver(input, check);
  if (check.class === 'security') throw new HarnessError('Security checks cannot be waived', 'WAIVER_FORBIDDEN');
  await appendLedger(config, LEDGER_FILE, waiver);
  return waiver;
}

export function latestEntries(entries, predicate) {
  return entries.filter(predicate).sort((left, right) => Date.parse(left.createdAt) - Date.parse(right.createdAt)).at(-1) ?? null;
}

export function verifyRecord(record, kind) {
  try {
    return { ok: true, record: validateIntegrity(record, kind) };
  } catch (error) {
    return { ok: false, reason: error.message };
  }
}

export async function verifyVerificationReceipt(config, record) {
  try {
    return { ok: true, record: await validateVerification(config, record) };
  } catch (error) {
    return { ok: false, reason: error.message };
  }
}
