import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { HarnessError, normalizeLf, readJson, runProcess, sha256, stableJson } from './common.mjs';
import { lintCatalog, loadCatalog } from './catalog.mjs';
import { loadMatrix } from './quality.mjs';

const HOOK_EVENTS = ['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'SubagentStart', 'SubagentStop', 'Stop', 'SessionEnd'];
const AGENT_NAMES = ['code-reviewer', 'deployer', 'evolution-runner', 'feedback-observer', 'impact-analyst', 'implementer', 'progress-recorder', 'researcher', 'tester'];
const READ_ONLY_AGENTS = new Set(['code-reviewer', 'evolution-runner', 'impact-analyst', 'researcher']);

async function readText(filePath) {
  try { return await readFile(filePath, 'utf8'); }
  catch (error) { throw new HarnessError(`Cannot read ${filePath}: ${error.message}`, 'DOCTOR_READ_FAILED'); }
}

async function managedDigest(filePath) {
  try {
    const bytes = await readFile(filePath);
    return bytes.includes(0) ? sha256(bytes) : sha256(Buffer.from(normalizeLf(bytes.toString('utf8')), 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

function frontmatter(text) {
  const match = text.match(/^---\s*\r?\n([\s\S]*?)\r?\n---(?:\s*\r?\n|$)/);
  if (!match) return null;
  return Object.fromEntries(match[1].split(/\r?\n/).map((line) => {
    const field = line.match(/^([A-Za-z][\w-]*):\s*(.+?)\s*$/);
    return field ? [field[1], field[2].replace(/^(?:"(.*)"|'(.*)')$/, '$1$2')] : null;
  }).filter(Boolean));
}

function quotedField(text, field) {
  const match = text.match(new RegExp(`^${field}\\s*=\\s*"([^"]+)"`, 'm'));
  return match?.[1] ?? null;
}

async function validateHookConfig(config) {
  const toml = await readText(path.join(config.repoRoot, '.codex', 'config.toml'));
  const failures = [];
  if (!/max_concurrent_threads_per_session\s*=\s*\d+/.test(toml)) failures.push('current agent concurrency field missing');
  if (/\b(?:max_threads|max_depth)\s*=/.test(toml)) failures.push('legacy max_threads/max_depth present');
  if (/git\s+rev-parse/i.test(toml)) failures.push('hook dispatcher must not depend on Git root discovery');
  if ([...toml.matchAll(/^type\s*=\s*"command"/gm)].length !== HOOK_EVENTS.length) failures.push('every hook must have one command handler');
  if ([...toml.matchAll(/^command_windows\s*=/gm)].length !== HOOK_EVENTS.length) failures.push('every hook needs command_windows');
  for (const event of HOOK_EVENTS) {
    const registrations = [...toml.matchAll(new RegExp(`\\[\\[hooks\\.${event}\\]\\]`, 'g'))].length;
    const handlers = [...toml.matchAll(new RegExp(`\\[\\[hooks\\.${event}\\.hooks\\]\\]`, 'g'))].length;
    if (registrations !== 1 || handlers !== 1) failures.push(`${event} registration=${registrations} handler=${handlers}`);
    if (!new RegExp(`harness\\.mjs[\\"']?\\s+hook\\s+${event}`).test(toml)) failures.push(`${event} dispatcher missing`);
    if (!new RegExp(`command_windows\\s*=.*harness\\.mjs.*hook\\s+${event}`).test(toml)) failures.push(`${event} Windows dispatcher missing`);
  }
  return { ok: failures.length === 0, failures };
}

async function validateAgents(config) {
  const directory = path.join(config.repoRoot, '.codex', 'agents');
  const files = (await readdir(directory)).filter((name) => name.endsWith('.toml')).sort();
  const failures = [];
  if (stableJson(files.map((name) => name.replace(/\.toml$/, '')).sort()) !== stableJson(AGENT_NAMES)) {
    failures.push(`expected agents: ${AGENT_NAMES.join(', ')}`);
  }
  for (const file of files) {
    const text = await readText(path.join(directory, file));
    const name = quotedField(text, 'name');
    const sandbox = quotedField(text, 'sandbox_mode');
    if (!name || !quotedField(text, 'description') || !/developer_instructions\s*=\s*"""[\s\S]+?"""/.test(text)) failures.push(`${file}: required field missing`);
    if (/^model\s*=/m.test(text)) failures.push(`${file}: fixed model is forbidden`);
    if (!/\[agents\][\s\S]*?enabled\s*=\s*false/.test(text)) failures.push(`${file}: child agents are not disabled`);
    const expectedSandbox = READ_ONLY_AGENTS.has(name) ? 'read-only' : 'workspace-write';
    if (sandbox !== expectedSandbox) failures.push(`${file}: sandbox=${sandbox ?? 'missing'} expected=${expectedSandbox}`);
  }
  return { ok: failures.length === 0, files: files.length, failures };
}

async function validateSkills(config) {
  const directory = path.join(config.repoRoot, '.agents', 'skills');
  const entries = await readdir(directory, { withFileTypes: true });
  const failures = [];
  let count = 0;
  for (const entry of entries.filter((item) => item.isDirectory()).sort((a, b) => a.name.localeCompare(b.name))) {
    count += 1;
    const file = path.join(directory, entry.name, 'SKILL.md');
    let text;
    try { text = await readFile(file, 'utf8'); }
    catch (error) { failures.push(`${entry.name}: SKILL.md missing (${error.message})`); continue; }
    const meta = frontmatter(text);
    if (!meta?.name || !meta?.description || meta.name !== entry.name) failures.push(`${entry.name}: invalid frontmatter`);
  }
  return { ok: failures.length === 0, count, failures };
}

async function validatePowerShell(config) {
  const directory = path.join(config.repoRoot, '.agents', 'scripts');
  const files = (await readdir(directory)).filter((name) => name.endsWith('.ps1'));
  const failures = [];
  for (const file of files) {
    const bytes = await readFile(path.join(directory, file));
    if ([...bytes].some((value) => value > 0x7f)) failures.push(`${file}: non-ASCII byte`);
  }
  return { ok: failures.length === 0, files: files.length, failures };
}

async function trackedRuntime(config) {
  const result = await runProcess('git', ['ls-files', '-z', '--', '.codex/harness-state'], { cwd: config.repoRoot, timeoutMs: 10000, maxOutput: 100000 });
  if (result.status === 'BLOCKED' || result.exitCode !== 0) return { ok: true, skipped: 'not a Git worktree' };
  const paths = result.stdout.split('\0').filter(Boolean).filter((item) => item !== '.codex/harness-state/.gitignore');
  return { ok: paths.length === 0, paths };
}

async function managedDrift(config) {
  const installManifestPath = path.join(config.statePath, 'install-manifest.json');
  const manifest = await readJson(installManifestPath, { required: false });
  if (!manifest) return { checked: false, critical: [], customized: [] };
  if (manifest.version !== 1 || !Array.isArray(manifest.files)) throw new HarnessError('Installed manifest is invalid', 'INSTALL_MANIFEST_INVALID');
  const critical = [];
  const customized = [];
  for (const entry of manifest.files) {
    const digest = await managedDigest(path.join(config.repoRoot, entry.path));
    if (digest === entry.sha256) continue;
    const isCritical = entry.path === '.codex/config.toml' || entry.path === '.codex/harness.json' || entry.path.startsWith('.codex/runtime/')
      || entry.path.startsWith('.codex/agents/') || entry.path.startsWith('.codex/rules/');
    (isCritical ? critical : customized).push(entry.path);
  }
  return { checked: true, critical, customized, manifest };
}

export async function validateHarness(config) {
  const catalog = await loadCatalog(config);
  const matrix = await loadMatrix(config);
  let receiptSchema;
  for (const schema of ['module-catalog.schema.json', 'verification-matrix.schema.json', 'receipt.schema.json', 'waiver.schema.json']) {
    const value = await readJson(path.join(config.repoRoot, '.codex', 'harness', 'schemas', schema));
    if (schema === 'receipt.schema.json') receiptSchema = value;
  }
  for (const field of ['evidencePath', 'evidenceBytes', 'evidenceHash']) {
    if (!receiptSchema?.required?.includes(field) || !receiptSchema?.properties?.[field]) {
      throw new HarnessError(`Receipt schema is missing evidence integrity field: ${field}`, 'VALIDATE_FAILED');
    }
  }
  const ignore = await readText(path.join(config.repoRoot, '.codex', 'harness-state', '.gitignore'));
  if (!ignore.includes('*') || !ignore.includes('!.gitignore')) throw new HarnessError('harness-state .gitignore is unsafe', 'VALIDATE_FAILED');
  const hooks = await validateHookConfig(config);
  if (!hooks.ok) throw new HarnessError(`Hook wire invalid: ${hooks.failures.join('; ')}`, 'VALIDATE_HOOK_WIRE');
  const agents = await validateAgents(config);
  if (!agents.ok) throw new HarnessError(`Agent config invalid: ${agents.failures.join('; ')}`, 'VALIDATE_AGENT_CONFIG');
  const skills = await validateSkills(config);
  if (!skills.ok) throw new HarnessError(`Skill metadata invalid: ${skills.failures.join('; ')}`, 'VALIDATE_SKILLS');
  return {
    ok: true,
    structureOnly: true,
    qualityReceiptWritten: false,
    catalogModules: catalog.modules.length,
    verificationChecks: matrix.checks.length,
    hookWire: { checked: true, events: HOOK_EVENTS.length },
    agents: agents.files,
    skills: skills.count
  };
}

export async function doctorHarness(config) {
  const errors = [];
  const warnings = [];
  const checks = [];
  const record = (name, ok, detail) => { checks.push({ name, ok, detail }); if (!ok) errors.push(`${name}: ${detail}`); };
  try { record('structure', true, JSON.stringify(await validateHarness(config))); }
  catch (error) { record('structure', false, error.message); }
  const agentsFile = path.join(config.repoRoot, 'AGENTS.md');
  const agentsBytes = (await readFile(agentsFile)).length;
  record('AGENTS.md', agentsBytes <= 32768, `${agentsBytes} bytes`);
  if (agentsBytes > 24576 && agentsBytes <= 32768) warnings.push(`AGENTS.md exceeds the preferred 24 KiB budget: ${agentsBytes}`);
  const powerShell = await validatePowerShell(config);
  record('PowerShell ASCII', powerShell.ok, powerShell.ok ? `${powerShell.files} files` : powerShell.failures.join('; '));
  const runtime = await trackedRuntime(config);
  record('runtime hygiene', runtime.ok, runtime.skipped ?? (runtime.ok ? 'no tracked runtime state' : runtime.paths.join(', ')));
  for (const [name, executable, args] of [['node', 'node', ['--version']], ['git', 'git', ['--version']], ['codex', 'codex', ['--version']]]) {
    const windowsShim = process.platform === 'win32' && name === 'codex';
    const result = await runProcess(windowsShim ? (process.env.ComSpec || 'cmd.exe') : executable,
      windowsShim ? ['/d', '/s', '/c', 'codex --version'] : args,
      { cwd: config.repoRoot, timeoutMs: 10000, maxOutput: 2000 });
    record(name, result.status === 'PASS', (result.stdout || result.stderr || result.error?.message || result.status).trim());
    if (name === 'codex' && result.status === 'PASS' && !/\b0\.146\.0\b/.test(result.stdout)) warnings.push(`Codex version differs from validated 0.146.0: ${result.stdout.trim()}`);
  }
  const drift = await managedDrift(config);
  if (drift.checked) {
    if (drift.critical.length) errors.push(`critical managed drift: ${drift.critical.join(', ')}`);
    if (drift.customized.length) warnings.push(`customized managed files: ${drift.customized.join(', ')}`);
  }
  const coverage = await lintCatalog(config);
  if (!coverage.ok) {
    const catalogEntry = drift.manifest?.files?.find((entry) => entry.path === '.codex/harness/module-catalog.json');
    const catalogDigest = await managedDigest(config.catalogPath);
    if (catalogEntry && catalogDigest === catalogEntry.sha256) warnings.push('Module catalog is still the scaffold bootstrap; customize it before relying on affected checks.');
    else errors.push(`catalog coverage failed: ${coverage.failures.slice(0, 10).map((item) => item.path).join(', ')}`);
  }
  const result = { ok: errors.length === 0, checks, warnings, errors, catalogHash: sha256(stableJson(await loadCatalog(config))) };
  return result;
}
