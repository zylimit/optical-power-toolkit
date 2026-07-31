import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { boundedText, HarnessError, normalizeRepoPath, pathExists, resolveForWrite } from './common.mjs';

const EVENTS = new Set(['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'SubagentStart', 'SubagentStop', 'Stop', 'SessionEnd']);

function deny(reason) {
  return {
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: reason
    }
  };
}

export function classifyDangerousCommand(command) {
  const text = String(command ?? '').replace(/\s+/g, ' ').trim();
  const rules = [
    [/\bgit\s+reset\s+--hard\b/i, 'git reset --hard can discard work'],
    [/\bgit\s+clean\b[^\n]*(?:-[^\s]*[fdx])/i, 'git clean with deletion flags can discard files'],
    [/\bRemove-Item\b[^\n]*(?:-Recurse[^\n]*-Force|-Force[^\n]*-Recurse)/i, 'recursive forced deletion is blocked'],
    [/\b(?:pkill|killall)\b|\bkill\s+-9\b|\btaskkill\b[^\n]*\/F/i, 'forced process termination requires explicit coordination'],
    [/\b(?:shutdown|reboot|Restart-Computer|Stop-Computer)\b/i, 'machine shutdown or restart is blocked'],
    [/\b(?:mkfs(?:\.[a-z0-9]+)?|diskpart|format\s+[A-Z]:)\b/i, 'disk formatting commands are blocked']
  ];
  for (const [pattern, reason] of rules) if (pattern.test(text)) return { dangerous: true, reason };
  for (const segment of shellSegments(command)) {
    const name = commandName(segment);
    const git = gitInvocation(segment);
    if (git?.subcommand === 'reset' && git.args.includes('--hard')) return { dangerous: true, reason: 'git reset --hard can discard work' };
    if (git?.subcommand === 'clean' && git.args.some((flag) => /^-[^-]*[fdx]/i.test(flag))) return { dangerous: true, reason: 'git clean with deletion flags can discard files' };
    const flags = segment.filter((token) => token.kind === 'word').slice(1)
      .filter((token) => token.value.startsWith('-') || token.value.startsWith('/')).map((token) => token.value);
    if (name === 'rm') {
      const recursive = flags.some((flag) => flag === '--recursive' || /^-[^-]*[rR]/.test(flag));
      const forced = flags.some((flag) => flag === '--force' || /^-[^-]*f/.test(flag));
      if (recursive && forced) return { dangerous: true, reason: 'recursive forced deletion is blocked' };
    }
    if (['del', 'erase', 'rd', 'rmdir'].includes(name)
      && flags.some((flag) => /^\/s$/i.test(flag)) && flags.some((flag) => /^\/q$/i.test(flag))) {
      return { dangerous: true, reason: 'recursive quiet deletion is blocked' };
    }
  }
  return { dangerous: false, reason: null };
}

export function patchPaths(command) {
  const result = [];
  const pattern = /^\*\*\* (?:Add|Update|Delete) File: (.+)$/gm;
  for (const match of String(command ?? '').matchAll(pattern)) result.push(match[1].trim());
  return [...new Set(result)];
}

function isApplyPatchTool(toolName) {
  return /(?:^|[._-])apply_patch$/i.test(toolName);
}

function isShellTool(toolName) {
  return /(?:^|[._-])(?:bash|pwsh|powershell|shell_command|exec_command)$/i.test(toolName);
}

function candidateWritePaths(toolName, toolInput) {
  if (isApplyPatchTool(toolName)) {
    const patch = typeof toolInput === 'string'
      ? toolInput
      : toolInput?.patch ?? toolInput?.command ?? toolInput?.input;
    return patchPaths(patch);
  }
  if (!toolInput || typeof toolInput !== 'object') return [];
  if (!/(?:write|edit|create|delete|move|rename|copy|apply_patch|remove)/i.test(toolName)) return [];
  const pathKeys = new Set(['path', 'paths', 'file', 'files', 'filepath', 'filepaths', 'target', 'targets', 'destination', 'destinations', 'to', 'literalpath', 'destinationpath']);
  if (/(?:delete|remove|move|rename|directory|mkdir)/i.test(toolName)) {
    for (const key of ['directory', 'dir', 'source', 'from']) pathKeys.add(key);
  }
  const result = [];
  const visit = (value, key = '') => {
    const normalizedKey = key.replace(/[-_]/g, '').toLowerCase();
    if (typeof value === 'string' && pathKeys.has(normalizedKey)) result.push(value);
    else if (Array.isArray(value)) value.forEach((item) => visit(item, key));
    else if (value && typeof value === 'object') for (const [childKey, child] of Object.entries(value)) visit(child, childKey);
  };
  visit(toolInput);
  return [...new Set(result)];
}

function toolResponseSucceeded(response) {
  if (response === true || response === 0) return true;
  if (typeof response === 'string') {
    if (/\b(?:error|failed|failure|blocked|denied|timed out)\b/i.test(response)) return false;
    return /^(?:done!?|ok\b|success(?:ful(?:ly)?)?\b)|(?:process|script) exited with (?:code )?0\b|exit code:\s*0\b/i.test(response.trim());
  }
  if (Array.isArray(response)) return response.some(toolResponseSucceeded);
  if (!response || typeof response !== 'object') return false;
  if (response.success === false || response.ok === false || response.is_error === true || response.isError === true || response.error) return false;
  for (const value of [response.exitCode, response.exit_code, response.code, response.metadata?.exitCode, response.metadata?.exit_code]) {
    if (value !== undefined && value !== null) return Number(value) === 0;
  }
  if (response.success === true || response.ok === true || response.is_error === false || response.isError === false) return true;
  if (/^(?:pass|passed|success|succeeded|completed|ok)$/i.test(String(response.status ?? response.outcome ?? ''))) return true;
  return [response.output, response.text, response.content, response.result].some(toolResponseSucceeded);
}

function shellTokens(command) {
  const tokens = [];
  let value = '';
  let quote = null;
  let dynamic = false;
  const push = () => {
    if (value) tokens.push({ kind: 'word', value, dynamic });
    value = '';
    dynamic = false;
  };
  const text = String(command ?? '');
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quote) {
      if (character === quote) quote = null;
      else {
        if (character === '$' || character === '`') dynamic = true;
        value += character;
      }
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (/\s/.test(character)) {
      push();
      if (character === '\n' || character === '\r') tokens.push({ kind: 'op', value: ';' });
      continue;
    }
    if (character === '>' || character === ';' || character === '|' || character === '&') {
      push();
      const pair = text.slice(index, index + 2);
      if (pair === '>>' || pair === '||' || pair === '&&') index += 1;
      tokens.push({ kind: character === '>' ? 'redirect' : 'op', value: pair === '>>' || pair === '||' || pair === '&&' ? pair : character });
      continue;
    }
    if (character === '$' || character === '`' || character === '*' || character === '?') dynamic = true;
    value += character;
  }
  push();
  return tokens;
}

function shellSegments(command) {
  const segments = [[]];
  for (const token of shellTokens(command)) {
    if (token.kind === 'op') segments.push([]);
    else segments.at(-1).push(token);
  }
  return segments.filter((segment) => segment.length);
}

function commandName(segment) {
  const first = segment.find((token) => token.kind === 'word');
  return first ? path.win32.basename(path.posix.basename(first.value)).toLowerCase().replace(/\.(?:exe|cmd|bat)$/i, '') : '';
}

function gitInvocation(segment) {
  if (commandName(segment) !== 'git') return null;
  const words = segment.filter((token) => token.kind === 'word').map((token) => token.value);
  let index = 1;
  const optionsWithValue = new Set(['-C', '-c', '--git-dir', '--work-tree', '--namespace', '--config-env', '--exec-path']);
  while (index < words.length && words[index].startsWith('-')) {
    const option = words[index];
    if (optionsWithValue.has(option)) index += 2;
    else index += 1;
  }
  if (index >= words.length) return { subcommand: null, args: [] };
  return { subcommand: words[index].toLowerCase(), args: words.slice(index + 1) };
}

function staticPath(token) {
  if (!token || token.kind !== 'word' || token.dynamic || !token.value || token.value.startsWith('-')) return null;
  if (/^%[^%]+%$/.test(token.value) || token.value.startsWith('~')) return null;
  return token.value;
}

function operands(segment, start = 1) {
  return segment.filter((token) => token.kind === 'word').slice(start).filter((token) => !token.value.startsWith('-'));
}

function parameterValues(words, names) {
  const result = [];
  for (let index = 1; index < words.length; index += 1) {
    const match = words[index].value.match(/^-([A-Za-z]+)(?:=|:)(.+)$/);
    if (match && names.has(match[1].toLowerCase())) result.push({ ...words[index], value: match[2] });
    else if (names.has(words[index].value.replace(/^-/, '').toLowerCase()) && words[index + 1]) result.push(words[++index]);
  }
  return result;
}

function shellWritePaths(command) {
  const result = [];
  const add = (token) => {
    const value = staticPath(token);
    if (value) result.push(value);
  };
  const tokens = shellTokens(command);
  for (let index = 0; index < tokens.length; index += 1) {
    if (tokens[index].kind === 'redirect' && tokens[index + 1]?.kind === 'word') add(tokens[index + 1]);
  }
  for (const segment of shellSegments(command)) {
    const name = commandName(segment);
    const words = segment.filter((token) => token.kind === 'word');
    if (name === 'tee' || name === 'touch' || name === 'mkdir' || name === 'rm' || name === 'del') operands(segment).forEach(add);
    else if (name === 'cp' || name === 'mv' || name === 'copy' || name === 'move') add(operands(segment).at(-1));
    else if (name === 'set-content' || name === 'add-content' || name === 'out-file' || name === 'new-item') {
      const named = parameterValues(words, new Set(['path', 'literalpath', 'filepath']));
      (named.length ? named : operands(segment).slice(0, 1)).forEach(add);
    } else if (name === 'copy-item' || name === 'move-item') {
      const named = parameterValues(words, new Set(['destination', 'destinationpath']));
      (named.length ? named : operands(segment).slice(-1)).forEach(add);
    } else if (name === 'remove-item') {
      const named = parameterValues(words, new Set(['path', 'literalpath']));
      (named.length ? named : operands(segment)).forEach(add);
    }
  }
  return [...new Set(result)];
}

function toolCandidatePath(config, toolInput, candidate) {
  if (path.isAbsolute(candidate)) return candidate;
  const requestedCwd = toolInput?.cwd ?? toolInput?.workdir ?? toolInput?.working_directory;
  const cwd = typeof requestedCwd === 'string' && !/[\$`*?]/.test(requestedCwd) ? requestedCwd : '.';
  return path.resolve(config.repoRoot, cwd, candidate);
}

function pathPolicyReason(config, relativePath) {
  const target = normalizeRepoPath(relativePath);
  const pieces = target.toLowerCase().split('/');
  const base = pieces.at(-1);
  if (pieces.includes('.git')) return 'writes to .git are blocked';
  if (config.security.dependencyDirs.some((item) => pieces.includes(item.toLowerCase()))) return 'writes to dependency/generated directories are blocked';
  if (config.security.allowedSecretTemplates.map((item) => item.toLowerCase()).includes(base)) return null;
  if (config.security.secretNames.map((item) => item.toLowerCase()).includes(base)) return 'writes to secret files are blocked';
  if (base.startsWith('.env')) return 'writes to environment secret files are blocked';
  if (config.security.secretExtensions.some((item) => base.endsWith(item.toLowerCase()))) return 'writes to private key/certificate files are blocked';
  return null;
}

async function validateWriteTarget(config, inputPath) {
  const resolved = await resolveForWrite(config.repoRoot, inputPath);
  const relative = path.relative(config.repoRoot, resolved.absolute).split(path.sep).join('/');
  const reason = pathPolicyReason(config, relative);
  if (reason) throw new HarnessError(`${reason}: ${relative}`, 'WRITE_BLOCKED');
  return normalizeRepoPath(relative);
}

async function preToolUse(config, input) {
  const toolName = String(input.tool_name ?? '');
  const toolInput = input.tool_input;
  const shellTool = isShellTool(toolName);
  if (shellTool) {
    const classified = classifyDangerousCommand(toolInput?.command);
    if (classified.dangerous) return deny(classified.reason);
  }
  try {
    const safePaths = [];
    if (shellTool) {
      for (const candidate of shellWritePaths(toolInput?.command)) {
        safePaths.push(await validateWriteTarget(config, toolCandidatePath(config, toolInput, candidate)));
      }
    }
    for (const candidate of candidateWritePaths(toolName, toolInput)) {
      safePaths.push(await validateWriteTarget(config, toolCandidatePath(config, toolInput, candidate)));
    }
    const uniquePaths = [...new Set(safePaths)];
    if (uniquePaths.length) await (await import('./tasks.mjs')).preflightTaskWrites(config, uniquePaths);
  } catch (error) {
    return deny(error.message);
  }
  return {};
}

async function postToolUse(config, input) {
  const { getTask, refreshTask } = await import('./tasks.mjs');
  const task = await getTask(config);
  if (!task) return {};
  const toolName = String(input.tool_name ?? '');
  const shellTool = isShellTool(toolName);
  const candidates = shellTool
    ? shellWritePaths(input.tool_input?.command).map((item) => toolCandidatePath(config, input.tool_input, item))
    : candidateWritePaths(toolName, input.tool_input).map((item) => toolCandidatePath(config, input.tool_input, item));
  if (!candidates.length || !toolResponseSucceeded(input.tool_response)) return {};
  const paths = candidates
    .map((item) => path.relative(config.repoRoot, path.resolve(config.repoRoot, item)).split(path.sep).join('/'))
    .filter((item) => item && !item.startsWith('../'));
  await refreshTask(config, paths);
  return {};
}

async function sessionStart(config) {
  const [{ getTask }, { fastModeStatus }, { changedPaths }] = await Promise.all([
    import('./tasks.mjs'), import('./quality.mjs'), import('./git.mjs')
  ]);
  const docs = ['progress.md', 'Product-Spec.md', 'Product-Spec-CHANGELOG.md', 'DEV-PLAN.md'];
  const [task, fast, changes, documentState, feedback] = await Promise.all([
    getTask(config),
    fastModeStatus(config),
    changedPaths(config.repoRoot),
    Promise.all(docs.map(async (file) => [file, await pathExists(path.join(config.repoRoot, file))])),
    feedbackStatus(config)
  ]);
  const lines = ['Codex Base harness is active. Hooks are safety guardrails, not an OS sandbox.'];
  if (task) lines.push(`Active task: ${task.id} (${task.risk}); owned paths: ${task.ownedPaths.join(', ')}`);
  if (fast.active) lines.push(`Fast Mode active until ${fast.expiresAt}; security controls remain active.`);
  if (fast.expired) lines.push(`Fast Mode expired at ${fast.expiresAt}; old SKIPPED receipts no longer satisfy completion.`);
  if (fast.invalid) lines.push('Fast Mode state is missing a window binding; quality skips are disabled until Fast Mode is re-enabled.');
  if (changes.paths.length) lines.push(`Working tree has ${changes.paths.length} changed path(s); preserve pre-existing user changes.`);
  const missingDocs = documentState.filter(([, exists]) => !exists).map(([file]) => file);
  if (missingDocs.length) lines.push(`Recap is degraded; missing project document(s): ${missingDocs.join(', ')}.`);
  if (feedback.total) lines.push(`Feedback: ${feedback.pending} pending of ${feedback.total}; evolution-runner may inspect proposals.`);
  return { hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext: boundedText(lines.join('\n'), config.outputLimits.hookChars) } };
}

async function feedbackStatus(config) {
  try {
    const text = await readFile(path.join(config.repoRoot, '.agents', 'feedback', 'FEEDBACK-INDEX.md'), 'utf8');
    const entries = text.split(/\r?\n/).filter((line) => /^-\s+/.test(line));
    const graduated = entries.filter((line) => /\[已毕业\]/.test(line)).length;
    return { total: entries.length, graduated, pending: entries.length - graduated };
  } catch (error) {
    if (error.code === 'ENOENT') return { total: 0, graduated: 0, pending: 0 };
    throw error;
  }
}

function userPromptSubmit(config, input) {
  const prompt = String(input.prompt ?? '');
  if (!/(?:你错了|不对，不是|不是这样|请纠正|以后不要|wrong,|that's wrong|do not do that again)/i.test(prompt)) return {};
  return {
    hookSpecificOutput: {
      hookEventName: 'UserPromptSubmit',
      additionalContext: boundedText('Potential user correction detected. Handle the request first, then ask the main Agent to route a deduplicated feedback-observer record if the signal is real.', config.outputLimits.hookChars)
    }
  };
}

async function subagentStart(config, input) {
  const task = await (await import('./tasks.mjs')).getTask(config);
  const context = [
    'Sub-agent depth is one: do not spawn another agent.',
    'Return the required Status/Changed/Verified/Not verified/Needs review by/Evidence envelope.',
    'A DONE message is not a verification or review receipt.'
  ];
  if (task) context.push(`Active task ${task.id}; only change owned paths: ${task.ownedPaths.join(', ')}`);
  return { hookSpecificOutput: { hookEventName: 'SubagentStart', additionalContext: boundedText(context.join('\n'), config.outputLimits.hookChars) } };
}

function subagentStop(config, input) {
  if (input.stop_hook_active) return {};
  const message = String(input.last_assistant_message ?? '');
  const required = ['Status', 'Changed', 'Verified', 'Not verified', 'Needs review by', 'Evidence'];
  const missing = required.filter((field) => !new RegExp(`(^|\\n)${field}\\s*:`, 'i').test(message));
  if (missing.length) return { decision: 'block', reason: boundedText(`Return the required handoff envelope. Missing: ${missing.join(', ')}.`, config.outputLimits.hookChars) };
  return {};
}

async function stop(config, input) {
  if (input.stop_hook_active) return {};
  const [{ getTask }, { completionStatus }] = await Promise.all([import('./tasks.mjs'), import('./quality.mjs')]);
  const task = await getTask(config);
  if (!task) return {};
  try {
    const status = await completionStatus(config, { taskId: task.id });
    if (status.complete) return {};
    const missing = status.checks.filter((item) => !item.acceptable).map((item) => `${item.id}: ${item.reason}`);
    if (!status.review.acceptable) missing.push(`review: ${status.review.reason}`);
    return { decision: 'block', reason: boundedText(`Active task ${task.id} is not complete. ${missing.join('; ')}. Run the planned gates or report a partial handoff.`, config.outputLimits.hookChars) };
  } catch (error) {
    return { decision: 'block', reason: boundedText(`Completion gate could not be evaluated: ${error.message}. Report the failure explicitly.`, config.outputLimits.hookChars) };
  }
}

async function sessionEnd(config, input) {
  await (await import('./state.mjs')).updateState(config, 'sessions.json', { version: 1, sessions: {} }, (state) => ({
    ...state,
    sessions: {
      ...state.sessions,
      [String(input.session_id ?? 'unknown')]: { endedAt: new Date().toISOString(), reason: input.reason ?? 'other' }
    }
  }));
  return {};
}

export async function dispatchHook(config, event, input) {
  if (!EVENTS.has(event)) throw new HarnessError(`Unsupported hook event: ${event}`, 'HOOK_EVENT_INVALID');
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    if (event === 'PreToolUse') return deny('Malformed PreToolUse input blocked fail-closed.');
    if (event === 'Stop' || event === 'SubagentStop') return { decision: 'block', reason: `Malformed ${event} input; report the hook failure.` };
    throw new HarnessError(`Malformed ${event} input`, 'HOOK_INPUT_INVALID');
  }
  if (input.hook_event_name && input.hook_event_name !== event) throw new HarnessError(`Hook event mismatch: expected ${event}, got ${input.hook_event_name}`, 'HOOK_EVENT_MISMATCH');
  switch (event) {
    case 'SessionStart': return sessionStart(config, input);
    case 'UserPromptSubmit': return userPromptSubmit(config, input);
    case 'PreToolUse': return preToolUse(config, input);
    case 'PostToolUse': return postToolUse(config, input);
    case 'SubagentStart': return subagentStart(config, input);
    case 'SubagentStop': return subagentStop(config, input);
    case 'Stop': return stop(config, input);
    case 'SessionEnd': return sessionEnd(config, input);
    default: return {};
  }
}

function limitStrings(value, limit) {
  if (typeof value === 'string') return boundedText(value, Math.min(limit, 3000));
  if (Array.isArray(value)) return value.slice(0, 20).map((item) => limitStrings(item, limit));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, limitStrings(item, limit)]));
  return value;
}

export function boundedHookOutput(config, output) {
  const limited = limitStrings(output, config.outputLimits.hookChars);
  const serialized = JSON.stringify(limited);
  if (serialized.length <= config.outputLimits.hookChars) return limited;
  if (limited.hookSpecificOutput?.additionalContext) {
    limited.hookSpecificOutput.additionalContext = boundedText(limited.hookSpecificOutput.additionalContext, Math.max(100, config.outputLimits.hookChars - 300));
    if (JSON.stringify(limited).length <= config.outputLimits.hookChars) return limited;
  }
  return output.hookSpecificOutput?.permissionDecision === 'deny'
    ? deny('Operation blocked; the detailed hook reason exceeded the output limit.')
    : { systemMessage: 'Hook output exceeded the configured limit.' };
}

export function malformedHookOutput(event) {
  if (event === 'PreToolUse') return deny('Malformed PreToolUse JSON blocked fail-closed.');
  if (event === 'Stop' || event === 'SubagentStop') return { decision: 'block', reason: `Malformed ${event} JSON; report the hook failure.` };
  return { systemMessage: `Malformed ${event} JSON.` };
}
