import { randomUUID } from 'node:crypto';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { boundedText, contentHash, HarnessError, readJson, runProcess, sha256, stableJson, TOOL_VERSION, atomicWrite } from './common.mjs';
import { analyzeImpact, loadCatalog } from './catalog.mjs';
import { gitFingerprint, gitInfo } from './git.mjs';
import { assertTaskBaseline, getTask, markTaskComplete } from './tasks.mjs';
import { qualityLedger, latestEntries, recordReview, recordVerification, recordWaiver, verifyRecord, verifyVerificationReceipt } from './receipts.mjs';
import { readState, stateFile, withFileLock, writeState } from './state.mjs';

const CHECK_CLASSES = new Set(['structure', 'lint', 'test', 'build', 'security', 'review', 'release']);

async function requireGitFreshness(config, operation) {
  const info = await gitInfo(config.repoRoot);
  if (!info.isGit) {
    throw new HarnessError(`Git freshness is unavailable for ${operation}; task-bound quality evidence is blocked`, 'NON_GIT_FRESHNESS_UNAVAILABLE', {
      status: 'BLOCKED',
      operation,
      note: info.note
    });
  }
  return info;
}

function checkCommand(check) {
  if (check.executable) return { executable: check.executable, args: check.args ?? [], shell: false };
  return { executable: check.command, args: [], shell: true };
}

export function validateMatrix(matrix) {
  if (!matrix || matrix.version !== 1 || !matrix.riskChecks || !Array.isArray(matrix.checks) || !Array.isArray(matrix.conservativeChecks)) {
    throw new HarnessError('Verification matrix structure is invalid', 'MATRIX_INVALID');
  }
  const riskNames = ['low', 'medium', 'high'];
  if (Object.keys(matrix.riskChecks).some((name) => !riskNames.includes(name))
    || riskNames.some((name) => !Array.isArray(matrix.riskChecks[name]) || matrix.riskChecks[name].some((id) => typeof id !== 'string'))) {
    throw new HarnessError('riskChecks must define only low/medium/high string arrays', 'MATRIX_INVALID');
  }
  if (matrix.conservativeChecks.some((id) => typeof id !== 'string')) throw new HarnessError('conservativeChecks must contain strings', 'MATRIX_INVALID');
  const ids = new Set();
  for (const check of matrix.checks) {
    const fields = new Set(['id', 'class', 'executable', 'args', 'command', 'cwd', 'platform', 'timeoutMs', 'dependencies', 'resourceLocks', 'required', 'allowFastSkip']);
    for (const key of Object.keys(check)) if (!fields.has(key)) throw new HarnessError(`Unknown check field ${check.id ?? '?'}: ${key}`, 'MATRIX_INVALID');
    if (typeof check.id !== 'string' || !check.id || ids.has(check.id)) throw new HarnessError(`Invalid or duplicate check id: ${check.id}`, 'MATRIX_INVALID');
    ids.add(check.id);
    if (!CHECK_CLASSES.has(check.class)) throw new HarnessError(`Invalid check class: ${check.id}`, 'MATRIX_INVALID');
    if ((!check.executable || typeof check.executable !== 'string') && (!check.command || typeof check.command !== 'string')) {
      throw new HarnessError(`Check needs executable or command: ${check.id}`, 'MATRIX_INVALID');
    }
    if (check.executable && check.command) throw new HarnessError(`Check cannot define executable and command: ${check.id}`, 'MATRIX_INVALID');
    if (check.executable && (!Array.isArray(check.args) || check.args.some((arg) => typeof arg !== 'string'))) throw new HarnessError(`Invalid args: ${check.id}`, 'MATRIX_INVALID');
    if (typeof check.cwd !== 'string' || !check.cwd || path.isAbsolute(check.cwd) || check.cwd.split(/[\\/]/).includes('..')) throw new HarnessError(`Invalid cwd: ${check.id}`, 'MATRIX_INVALID');
    if (!Number.isInteger(check.timeoutMs) || check.timeoutMs <= 0) throw new HarnessError(`Invalid timeout: ${check.id}`, 'MATRIX_INVALID');
    if (!Array.isArray(check.platform) || check.platform.length === 0 || check.platform.some((item) => !['win32', 'linux', 'darwin'].includes(item))) {
      throw new HarnessError(`Invalid platform: ${check.id}`, 'MATRIX_INVALID');
    }
    for (const field of ['dependencies', 'resourceLocks']) if (!Array.isArray(check[field]) || check[field].some((item) => typeof item !== 'string')) throw new HarnessError(`Invalid ${field}: ${check.id}`, 'MATRIX_INVALID');
    if (typeof check.required !== 'boolean' || typeof check.allowFastSkip !== 'boolean') throw new HarnessError(`Invalid required/allowFastSkip: ${check.id}`, 'MATRIX_INVALID');
    if (check.class === 'security' && check.allowFastSkip) throw new HarnessError(`Security check cannot allow Fast skip: ${check.id}`, 'MATRIX_INVALID');
  }
  const referenced = [...Object.values(matrix.riskChecks).flat(), ...matrix.conservativeChecks];
  for (const id of referenced) if (!ids.has(id)) throw new HarnessError(`Unknown matrix check: ${id}`, 'MATRIX_UNKNOWN_CHECK');
  for (const check of matrix.checks) for (const id of check.dependencies) if (!ids.has(id)) throw new HarnessError(`Unknown dependency ${id} in ${check.id}`, 'MATRIX_UNKNOWN_CHECK');
  topologicalOrder(matrix.checks);
  return matrix;
}

export async function loadMatrix(config) {
  return validateMatrix(await readJson(config.verificationPath));
}

export function topologicalOrder(checks) {
  const byId = new Map(checks.map((check) => [check.id, check]));
  const temporary = new Set();
  const permanent = new Set();
  const result = [];
  function visit(id) {
    if (permanent.has(id)) return;
    if (temporary.has(id)) throw new HarnessError(`Verification dependency cycle at ${id}`, 'MATRIX_CYCLE');
    temporary.add(id);
    for (const dependency of byId.get(id)?.dependencies ?? []) visit(dependency);
    temporary.delete(id);
    permanent.add(id);
    if (byId.has(id)) result.push(byId.get(id));
  }
  for (const id of byId.keys()) visit(id);
  return result;
}

export async function verificationPlan(config, options = {}) {
  const task = options.task ?? await getTask(config, options.taskId);
  if (!task) throw new HarnessError('Verification plan requires a task', 'TASK_NOT_FOUND');
  await requireGitFreshness(config, 'verification-plan');
  await assertTaskBaseline(config, task);
  const [matrix, catalog, impact, fingerprint] = await Promise.all([
    loadMatrix(config),
    loadCatalog(config),
    analyzeImpact(config, options.paths ? { paths: options.paths, truncated: options.truncated } : {}),
    gitFingerprint(config.repoRoot)
  ]);
  if (fingerprint.baseCommit !== task.baseline.baseCommit) await assertTaskBaseline(config, task);
  const checkIds = new Set(matrix.riskChecks[task.risk] ?? []);
  const reasons = {};
  for (const id of checkIds) reasons[id] = [`risk:${task.risk}`];
  for (const module of catalog.modules.filter((item) => impact.affectedModules.includes(item.id))) {
    for (const id of module.verification) {
      checkIds.add(id);
      (reasons[id] ??= []).push(`module:${module.id}`);
    }
  }
  if (impact.expandedToAll) {
    for (const id of matrix.conservativeChecks) {
      checkIds.add(id);
      (reasons[id] ??= []).push('conservative-impact');
    }
  }
  const byId = new Map(matrix.checks.map((check) => [check.id, check]));
  const includeDependencies = (id) => {
    const check = byId.get(id);
    if (!check) throw new HarnessError(`Catalog references unknown check: ${id}`, 'MATRIX_UNKNOWN_CHECK');
    for (const dependency of check.dependencies) {
      if (!checkIds.has(dependency)) {
        checkIds.add(dependency);
        reasons[dependency] = [`dependency-of:${id}`];
      }
      includeDependencies(dependency);
    }
  };
  for (const id of [...checkIds]) includeDependencies(id);
  const checks = topologicalOrder(matrix.checks).filter((check) => checkIds.has(check.id)).map((check) => ({ ...check, reasons: reasons[check.id] ?? [] }));
  const base = {
    version: 1,
    taskId: task.id,
    risk: task.risk,
    baseCommit: fingerprint.baseCommit,
    fingerprint: fingerprint.fingerprint,
    diffHash: fingerprint.diffHash,
    impactHash: sha256(stableJson(impact)),
    affectedModules: impact.affectedModules,
    expandedToAll: impact.expandedToAll,
    checks
  };
  return { ...base, planHash: sha256(stableJson(base)), impact };
}

export async function fastModeStatus(config, now = Date.now()) {
  const state = await readState(config, 'fast-mode.json', { version: 1, enabled: false, enabledAt: null, expiresAt: null, windowId: null });
  const expires = state.expiresAt ? Date.parse(state.expiresAt) : 0;
  const validWindow = typeof state.windowId === 'string' && state.windowId.length > 0;
  return {
    ...state,
    active: Boolean(state.enabled && validWindow && expires > now),
    expired: Boolean(state.enabled && expires <= now),
    invalid: Boolean(state.enabled && expires > now && !validWindow)
  };
}

export async function setFastMode(config, action, hours = 24) {
  if (action === 'status') return fastModeStatus(config);
  if (action === 'off') return writeState(config, 'fast-mode.json', { version: 1, enabled: false, enabledAt: null, expiresAt: null, windowId: null, updatedAt: new Date().toISOString() });
  if (action !== 'on' || !Number.isFinite(hours) || hours < 1 || hours > 720) throw new HarnessError('Fast Mode hours must be between 1 and 720', 'FAST_INVALID');
  const enabledAt = new Date().toISOString();
  return writeState(config, 'fast-mode.json', {
    version: 1,
    enabled: true,
    enabledAt,
    expiresAt: new Date(Date.now() + hours * 3600000).toISOString(),
    windowId: randomUUID(),
    updatedAt: enabledAt
  });
}

async function toolVersion(command, config) {
  if (command.executable === 'node' || path.basename(command.executable).startsWith('node')) return process.version;
  const result = await runProcess(command.executable, ['--version'], { cwd: config.repoRoot, timeoutMs: 2000, maxOutput: 2000 });
  return result.status === 'PASS' ? boundedText(result.stdout || result.stderr, 500).trim() : 'unavailable';
}

async function withResourceLocks(config, names, callback, index = 0) {
  const sorted = [...new Set(names)].sort();
  if (index >= sorted.length) return callback();
  const lockPath = stateFile(config, path.join('resource-locks', `${sorted[index].replace(/[^A-Za-z0-9_.-]/g, '_')}.lock`));
  return withFileLock(lockPath, config.locks, () => withResourceLocks(config, sorted, callback, index + 1));
}

async function executeCheck(config, task, plan, check, dependencyResults, fast, executor) {
  const command = checkCommand(check);
  const cwd = path.resolve(config.repoRoot, check.cwd);
  const argvHash = sha256(stableJson({ executable: command.executable, args: command.args, shell: command.shell }));
  const base = {
    version: 1,
    kind: 'verification',
    taskId: task.id,
    baseCommit: plan.baseCommit,
    fingerprint: plan.fingerprint,
    diffHash: plan.diffHash,
    planHash: plan.planHash,
    checkId: check.id,
    argvHash,
    argv: command.shell ? [command.executable] : [command.executable, ...command.args],
    cwd: path.relative(config.repoRoot, cwd).split(path.sep).join('/') || '.',
    toolVersion: TOOL_VERSION,
    modules: plan.affectedModules,
    module: plan.affectedModules,
    class: check.class,
    executorRole: executor.role,
    executorId: executor.id,
    createdAt: new Date().toISOString()
  };
  let result;
  let reason = '';
  let fastModeWindow = null;
  if (check.dependencies.some((id) => !['PASS', 'SKIPPED'].includes(dependencyResults.get(id)?.status))) {
    result = { status: 'BLOCKED', exitCode: null, durationMs: 0, stdout: '', stderr: '' };
    reason = 'dependency did not pass';
  } else if (check.platform?.length && !check.platform.includes(process.platform)) {
    result = { status: 'BLOCKED', exitCode: null, durationMs: 0, stdout: '', stderr: '' };
    reason = `platform ${process.platform} is not supported`;
  } else if (fast.active && check.allowFastSkip && check.class !== 'security') {
    result = { status: 'SKIPPED', exitCode: null, durationMs: 0, stdout: '', stderr: '' };
    reason = `Fast Mode active until ${fast.expiresAt}`;
    fastModeWindow = fast.windowId;
  } else {
    const version = await toolVersion(command, config);
    base.toolVersion = `${TOOL_VERSION}; ${version}`;
    try {
      result = await withResourceLocks(config, check.resourceLocks, () => runProcess(command.executable, command.args, {
        cwd,
        shell: command.shell,
        timeoutMs: check.timeoutMs,
        maxOutput: config.outputLimits.evidenceChars
      }));
    } catch (error) {
      if (error.code === 'LOCK_TIMEOUT') {
        result = { status: 'BLOCKED', exitCode: null, durationMs: 0, stdout: '', stderr: '' };
        reason = error.message;
      } else throw error;
    }
    if (result.timedOut) reason = `timed out after ${check.timeoutMs}ms`;
    else if (result.error) reason = result.error.message;
  }
  const evidence = boundedText([reason, result.stdout, result.stderr].filter(Boolean).join('\n'), config.outputLimits.evidenceChars);
  const evidencePath = stateFile(config, path.join('evidence', task.id, `${check.id}-${Date.now()}-${process.pid}.log`));
  await mkdir(path.dirname(evidencePath), { recursive: true });
  await atomicWrite(evidencePath, `${evidence}\n`);
  const evidenceBytes = await readFile(evidencePath);
  const receipt = {
    ...base,
    fastModeWindow,
    status: result.status,
    exitCode: result.exitCode,
    signal: result.signal ?? null,
    durationMs: result.durationMs,
    reason,
    evidencePath: path.relative(config.repoRoot, evidencePath).split(path.sep).join('/'),
    evidenceBytes: evidenceBytes.length,
    evidenceHash: sha256(evidenceBytes)
  };
  const complete = { ...receipt, contentHash: contentHash(receipt) };
  await recordVerification(config, complete);
  return complete;
}

export async function runGate(config, options = {}) {
  const task = await getTask(config, options.taskId);
  if (!task) throw new HarnessError('Gate requires an active or explicit task', 'TASK_NOT_FOUND');
  await requireGitFreshness(config, 'gate');
  const plan = await verificationPlan(config, { task, paths: options.paths });
  const selected = options.checkIds?.length ? new Set(options.checkIds) : null;
  if (selected && [...selected].some((id) => !plan.checks.some((check) => check.id === id))) throw new HarnessError('Requested check is not in the current plan', 'CHECK_NOT_PLANNED');
  if (selected) {
    const byId = new Map(plan.checks.map((check) => [check.id, check]));
    const addDependencies = (id) => {
      for (const dependency of byId.get(id)?.dependencies ?? []) {
        selected.add(dependency);
        addDependencies(dependency);
      }
    };
    for (const id of [...selected]) addDependencies(id);
  }
  const wanted = selected ? plan.checks.filter((check) => selected.has(check.id)) : plan.checks;
  const fast = await fastModeStatus(config);
  const executor = {
    role: String(options.executorRole ?? 'main-agent'),
    id: String(options.executorId ?? options.executorRole ?? 'main-agent')
  };
  if (!/^[a-z][a-z0-9-]{0,31}$/.test(executor.role) || !executor.id.trim() || executor.id.length > 200) {
    throw new HarnessError('Gate executor role/id are invalid', 'GATE_EXECUTOR_INVALID');
  }
  const results = new Map();
  for (const check of wanted) results.set(check.id, await executeCheck(config, task, plan, check, results, fast, executor));
  return { plan, receipts: [...results.values()], ok: [...results.values()].every((item) => item.status === 'PASS' || item.status === 'SKIPPED') };
}

export async function createReview(config, input) {
  const task = await getTask(config, input.taskId);
  if (!task) throw new HarnessError('Review task not found', 'TASK_NOT_FOUND');
  await requireGitFreshness(config, 'review');
  await assertTaskBaseline(config, task);
  const fingerprint = await gitFingerprint(config.repoRoot);
  if (fingerprint.baseCommit !== task.baseline.baseCommit) await assertTaskBaseline(config, task);
  return recordReview(config, { ...input, taskId: task.id, baseCommit: fingerprint.baseCommit, diffHash: fingerprint.diffHash, scope: input.scope ?? task.ownedPaths, exclusions: input.exclusions ?? task.reviewExclusions, findings: input.findings ?? [], notReviewed: input.notReviewed ?? [] });
}

export async function createQualityWaiver(config, input) {
  const task = await getTask(config, input.taskId);
  if (!task) throw new HarnessError('Waiver task not found', 'TASK_NOT_FOUND');
  const [matrix, fingerprint] = await Promise.all([loadMatrix(config), gitFingerprint(config.repoRoot)]);
  const check = matrix.checks.find((item) => item.id === input.checkId);
  if (!check) throw new HarnessError(`Unknown check: ${input.checkId}`, 'MATRIX_UNKNOWN_CHECK');
  return recordWaiver(config, { ...input, taskId: task.id, fingerprint: fingerprint.fingerprint }, check);
}

function validWaiver(entries, task, plan, check, now) {
  if (check.class === 'security') return null;
  const waiver = latestEntries(entries, (entry) => entry.kind === 'waiver' && entry.taskId === task.id && entry.checkId === check.id && entry.fingerprint === plan.fingerprint);
  if (!waiver) return null;
  const verified = verifyRecord(waiver, 'waiver');
  if (!verified.ok || Date.parse(waiver.expiresAt) <= now) return null;
  return waiver;
}

export async function completionStatus(config, options = {}) {
  const task = await getTask(config, options.taskId);
  if (!task) throw new HarnessError('Completion status requires a task', 'TASK_NOT_FOUND');
  await requireGitFreshness(config, 'completion');
  const [plan, ledger, fast] = await Promise.all([verificationPlan(config, { task }), qualityLedger(config), fastModeStatus(config)]);
  const now = Date.now();
  const checks = [];
  for (const check of plan.checks.filter((item) => item.required)) {
    const receipt = latestEntries(ledger.entries, (entry) => entry.kind === 'verification' && entry.taskId === task.id && entry.checkId === check.id && entry.fingerprint === plan.fingerprint && entry.planHash === plan.planHash);
    let acceptable = false;
    let reason = 'missing receipt';
    let receiptVerified = null;
    if (receipt) {
      receiptVerified = await verifyVerificationReceipt(config, receipt);
      if (!receiptVerified.ok) reason = receiptVerified.reason;
      else if (receipt.baseCommit !== plan.baseCommit || receipt.diffHash !== plan.diffHash) reason = 'stale Git binding';
      else if (receipt.status === 'PASS') { acceptable = true; reason = 'fresh PASS'; }
      else if (receipt.status === 'SKIPPED' && check.class !== 'security' && check.allowFastSkip && fast.active
        && receipt.fastModeWindow === fast.windowId && Date.parse(receipt.createdAt) <= Date.parse(fast.expiresAt)) {
        acceptable = true;
        reason = 'active Fast Mode SKIPPED';
      }
      else reason = `latest receipt is ${receipt.status}`;
      if (acceptable && task.risk === 'high' && !fast.active && receipt.executorRole !== 'tester') {
        acceptable = false;
        reason = 'high-risk checks require a fresh tester-executed receipt';
      }
    }
    const waiver = acceptable || (receipt && !receiptVerified.ok)
      ? null
      : validWaiver(ledger.entries, task, plan, check, now);
    if (waiver) { acceptable = true; reason = 'fresh quality waiver'; }
    checks.push({ id: check.id, class: check.class, acceptable, reason, receipt: receipt?.contentHash ?? null, waiver: waiver?.contentHash ?? null });
  }
  let review = { required: false, acceptable: true, reason: 'not required' };
  if (task.requiresReview && ['medium', 'high'].includes(task.risk) && !fast.active) {
    const receipt = latestEntries(ledger.entries, (entry) => entry.kind === 'review' && entry.taskId === task.id && entry.baseCommit === plan.baseCommit && entry.diffHash === plan.diffHash);
    review = { required: true, acceptable: false, reason: 'missing review receipt', receipt: receipt?.contentHash ?? null };
    if (receipt) {
      const verified = verifyRecord(receipt, 'review');
      const blocking = receipt.findings.some((item) => ['blocker', 'high'].includes(String(item.severity ?? '').toLowerCase()) && item.resolved !== true);
      const scopeMatches = stableJson(receipt.scope) === stableJson([...task.ownedPaths].sort()) && stableJson(receipt.exclusions) === stableJson([...task.reviewExclusions].sort());
      review = { required: true, acceptable: verified.ok && scopeMatches && receipt.decision === 'APPROVE' && !blocking, reason: !verified.ok ? verified.reason : !scopeMatches ? 'review scope or exclusions are stale' : blocking ? 'blocking findings remain' : receipt.decision !== 'APPROVE' ? `review decision ${receipt.decision}` : 'fresh approved review', receipt: receipt.contentHash };
    }
  }
  await assertTaskBaseline(config, task);
  return { taskId: task.id, fingerprint: plan.fingerprint, planHash: plan.planHash, checks, review, complete: checks.every((item) => item.acceptable) && review.acceptable };
}

export async function completeTask(config, taskId = undefined) {
  const task = await getTask(config, taskId);
  if (!task) throw new HarnessError('Task not found', 'TASK_NOT_FOUND');
  const status = await completionStatus(config, { taskId: task.id });
  if (!status.complete) throw new HarnessError('Task completion gate is not satisfied', 'COMPLETION_BLOCKED', status);
  return markTaskComplete(config, task.id, status);
}
