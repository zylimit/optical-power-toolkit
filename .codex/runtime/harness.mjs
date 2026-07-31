#!/usr/bin/env node
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { HarnessError, boundedText, parseCliArgs, readJson } from './lib/common.mjs';
import { loadConfig } from './lib/config.mjs';

async function stdinText() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
}

function csv(value) {
  if (Array.isArray(value)) return value;
  return String(value ?? '').split(',').map((item) => item.trim()).filter(Boolean);
}

function modelBounded(config, result) {
  const chars = `${JSON.stringify(result, null, 2)}\n`.length;
  if (chars > config.outputLimits.modelChars) {
    throw new HarnessError(`Model-visible output exceeds outputLimits.modelChars: ${chars} > ${config.outputLimits.modelChars}`, 'MODEL_OUTPUT_LIMIT');
  }
  return result;
}

async function jsonInput(flags) {
  if (flags.json && flags.json !== true) return readJson(path.resolve(String(flags.json)));
  const text = await stdinText();
  if (!text.trim()) throw new HarnessError('JSON input is required via --json file or stdin', 'CLI_INPUT_REQUIRED');
  try { return JSON.parse(text); } catch (error) { throw new HarnessError(`Invalid stdin JSON: ${error.message}`, 'CLI_INPUT_INVALID'); }
}

export async function runCli(argv = process.argv.slice(2), providedConfig = undefined) {
  const { positional, flags } = parseCliArgs(argv);
  const config = providedConfig ?? await loadConfig(flags.root ? path.resolve(String(flags.root)) : undefined);
  const [command, subcommand, extra] = positional;
  switch (command) {
    case 'validate': return (await import('./lib/doctor.mjs')).validateHarness(config);
    case 'doctor': return (await import('./lib/doctor.mjs')).doctorHarness(config);
    case 'fingerprint': return (await import('./lib/git.mjs')).gitFingerprint(config.repoRoot);
    case 'task': {
      const { cancelTask, getTask, startTask, taskState } = await import('./lib/tasks.mjs');
      if (subcommand === 'start') {
        const input = flags.json || !process.stdin.isTTY ? await jsonInput(flags) : {
          id: flags.id, goal: flags.goal, scope: flags.scope, outOfScope: flags['out-of-scope'], risk: flags.risk, ownedPaths: csv(flags.owned)
        };
        return startTask(config, input);
      }
      if (subcommand === 'status') return { state: await taskState(config), active: await getTask(config) };
      if (subcommand === 'cancel') return cancelTask(config, flags.reason ?? '');
      if (subcommand === 'complete') return (await import('./lib/quality.mjs')).completeTask(config, flags.id);
      throw new HarnessError(`Unknown task command: ${subcommand}`, 'CLI_UNKNOWN_COMMAND');
    }
    case 'catalog': {
      const { discoverModuleCandidates, lintCatalog } = await import('./lib/catalog.mjs');
      if (subcommand === 'lint') return lintCatalog(config, csv(flags.paths));
      if (subcommand === 'discover') return discoverModuleCandidates(config);
      throw new HarnessError(`Unknown catalog command: ${subcommand}`, 'CLI_UNKNOWN_COMMAND');
    }
    case 'repo-map': {
      const { discoverModuleCandidates, lintCatalog } = await import('./lib/catalog.mjs');
      return { lint: await lintCatalog(config), discovered: await discoverModuleCandidates(config) };
    }
    case 'affected': {
      const { analyzeImpact } = await import('./lib/catalog.mjs');
      return analyzeImpact(config, flags.paths
        ? { paths: csv(flags.paths), truncated: Boolean(flags.truncated) }
        : flags.baseline ? { baseline: String(flags.baseline) } : {});
    }
    case 'context':
      if (subcommand === 'pack') return modelBounded(config, await (await import('./lib/context.mjs')).buildContextPack(config, { taskId: flags.task, paths: flags.paths ? csv(flags.paths) : undefined }));
      throw new HarnessError(`Unknown context command: ${subcommand}`, 'CLI_UNKNOWN_COMMAND');
    case 'lease': {
      const { acquireLease, leaseStatus, releaseLease } = await import('./lib/leases.mjs');
      if (subcommand === 'acquire') return acquireLease(config, {
        paths: flags.paths,
        owner: flags.owner,
        taskId: flags.task,
        worktree: flags.worktree,
        integrationOwner: flags['integration-owner'],
        hours: flags.hours ?? 24
      });
      if (subcommand === 'status') return leaseStatus(config, { owner: flags.owner, taskId: flags.task });
      if (subcommand === 'release') return releaseLease(config, { id: flags.id, owner: flags.owner });
      throw new HarnessError(`Unknown lease command: ${subcommand}`, 'CLI_UNKNOWN_COMMAND');
    }
    case 'verify-plan': return (await import('./lib/quality.mjs')).verificationPlan(config, { taskId: flags.task, paths: flags.paths ? csv(flags.paths) : undefined });
    case 'gate': return (await import('./lib/quality.mjs')).runGate(config, {
        taskId: flags.task,
        checkIds: extra ? [subcommand, extra].filter(Boolean) : subcommand ? [subcommand] : undefined,
        executorRole: flags['executor-role'],
        executorId: flags['executor-id']
      });
    case 'quality':
      if (subcommand === 'status') return (await import('./lib/quality.mjs')).completionStatus(config, { taskId: flags.task });
      throw new HarnessError(`Unknown quality command: ${subcommand}`, 'CLI_UNKNOWN_COMMAND');
    case 'review':
      if (subcommand === 'record') return (await import('./lib/quality.mjs')).createReview(config, await jsonInput(flags));
      throw new HarnessError(`Unknown review command: ${subcommand}`, 'CLI_UNKNOWN_COMMAND');
    case 'waiver':
      if (subcommand === 'record') return (await import('./lib/quality.mjs')).createQualityWaiver(config, await jsonInput(flags));
      throw new HarnessError(`Unknown waiver command: ${subcommand}`, 'CLI_UNKNOWN_COMMAND');
    case 'fast': return (await import('./lib/quality.mjs')).setFastMode(config, subcommand ?? 'status', Number(flags.hours ?? extra ?? 24));
    case 'hook': {
      const { boundedHookOutput, dispatchHook, malformedHookOutput } = await import('./lib/hooks.mjs');
      const event = subcommand;
      const text = await stdinText();
      let input;
      try { input = JSON.parse(text); } catch { return malformedHookOutput(event); }
      return boundedHookOutput(config, await dispatchHook(config, event, input));
    }
    default: throw new HarnessError(`Unknown command: ${command ?? '<none>'}`, 'CLI_UNKNOWN_COMMAND');
  }
}

async function main() {
  try {
    const result = await runCli();
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    if (result?.ok === false) process.exitCode = 1;
  } catch (error) {
    const payload = {
      ok: false,
      error: error.code ?? 'UNEXPECTED_ERROR',
      message: boundedText(error.message, 4000),
      details: error.details
    };
    process.stderr.write(`${JSON.stringify(payload, null, 2)}\n`);
    process.exitCode = 1;
  }
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) await main();
