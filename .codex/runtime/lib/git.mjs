import { lstat, readFile, realpath } from 'node:fs/promises';
import path from 'node:path';
import { HarnessError, isPathInside, normalizeLf, normalizeRepoPath, runProcess, sha256, toPosix } from './common.mjs';

const STATE_ROOT = '.codex/harness-state/';
const STATE_POLICY = `${STATE_ROOT}.gitignore`;
const UNTRACKED_SYMLINK_TYPE = 'symlink:not-followed';
const UNTRACKED_SPECIAL_TYPE = 'special:not-read';
export const NON_GIT_BINDING = Object.freeze({
  baseCommit: 'DEGRADED:NON_GIT',
  diffHash: 'DEGRADED:NON_GIT:NO_CANONICAL_DIFF',
  fingerprint: 'DEGRADED:NON_GIT:NO_FINGERPRINT'
});

async function git(repoRoot, args, { allowFailure = false, timeoutMs = 30000 } = {}) {
  const result = await runProcess('git', args, { cwd: repoRoot, timeoutMs, maxOutput: 20_000_000 });
  if (result.status === 'BLOCKED') {
    if (allowFailure) return result;
    throw new HarnessError(`Git could not run: ${result.error?.message ?? result.stderr}`, 'GIT_BLOCKED');
  }
  if (result.exitCode !== 0 && !allowFailure) {
    throw new HarnessError(`Git failed (${args.join(' ')}): ${result.stderr.trim()}`, 'GIT_FAILED');
  }
  return result;
}

export async function gitInfo(repoRoot) {
  const inside = await git(repoRoot, ['rev-parse', '--is-inside-work-tree'], { allowFailure: true });
  if (inside.exitCode !== 0 || inside.stdout.trim() !== 'true') {
    return { isGit: false, baseCommit: NON_GIT_BINDING.baseCommit, unborn: false, note: 'not a Git worktree; Git guarantees are degraded' };
  }
  const head = await git(repoRoot, ['rev-parse', '--verify', 'HEAD'], { allowFailure: true });
  return {
    isGit: true,
    baseCommit: head.exitCode === 0 ? head.stdout.trim() : 'UNBORN',
    unborn: head.exitCode !== 0,
    note: head.exitCode === 0 ? null : 'unborn repository'
  };
}

function splitZero(value) {
  return value.split('\0').filter(Boolean).map((item) => toPosix(item));
}

// Runtime output must never bind quality evidence, but its tracked ignore policy must.
function excludeRuntimeState(paths) {
  return paths.filter((item) => item === STATE_POLICY || !item.startsWith(STATE_ROOT));
}

async function nameList(repoRoot, args) {
  const result = await git(repoRoot, args);
  return splitZero(result.stdout);
}

export async function changedPaths(repoRoot) {
  const info = await gitInfo(repoRoot);
  if (!info.isGit) return { ...info, paths: [], staged: [], unstaged: [], untracked: [] };
  const [rawStaged, rawUnstaged, rawUntracked] = await Promise.all([
    nameList(repoRoot, ['diff', '--cached', '--name-only', '-z', '--', '.']),
    nameList(repoRoot, ['diff', '--name-only', '-z', '--', '.']),
    nameList(repoRoot, ['ls-files', '--others', '--exclude-standard', '-z', '--', '.'])
  ]);
  const [staged, unstaged, untracked] = [rawStaged, rawUnstaged, rawUntracked].map(excludeRuntimeState);
  const paths = [...new Set([...staged, ...unstaged, ...untracked])].sort();
  return { ...info, paths, staged: [...new Set(staged)].sort(), unstaged: [...new Set(unstaged)].sort(), untracked: [...new Set(untracked)].sort() };
}

export async function changedPathsFromBaseline(repoRoot, baseline) {
  const value = String(baseline ?? '').trim();
  if (!value || value.startsWith('-') || /[\s\0]/.test(value)) {
    throw new HarnessError('Git baseline must be one revision without whitespace or option syntax', 'GIT_BASELINE_INVALID');
  }
  const info = await gitInfo(repoRoot);
  if (!info.isGit) return { ...info, baseline: value, paths: [], untracked: [], truncated: false };
  const verified = await git(repoRoot, ['rev-parse', '--verify', '--end-of-options', `${value}^{commit}`], { allowFailure: true });
  if (verified.exitCode !== 0) throw new HarnessError(`Invalid Git baseline: ${value}`, 'GIT_BASELINE_INVALID');
  const [rawChanged, rawUntracked] = await Promise.all([
    nameList(repoRoot, ['diff', '--name-only', '--no-renames', '--diff-filter=ACDMRTUXB', '-z', verified.stdout.trim(), '--', '.']),
    nameList(repoRoot, ['ls-files', '--others', '--exclude-standard', '-z', '--', '.'])
  ]);
  const [changed, untracked] = [rawChanged, rawUntracked].map(excludeRuntimeState);
  return {
    ...info,
    baseline: verified.stdout.trim(),
    paths: [...new Set([...changed, ...untracked])].sort(),
    untracked: [...new Set(untracked)].sort(),
    note: `diff from ${value}`
  };
}

async function diffBytes(repoRoot, args, paths) {
  if (!paths.length) return Buffer.alloc(0);
  // Paths came from repository metadata, so force literal pathspecs before diffing them.
  const result = await git(repoRoot, [...args, '--', ...paths.map((item) => `:(literal)${item}`)]);
  return Buffer.from(normalizeLf(result.stdout), 'utf8');
}

async function untrackedLinkRoot(repoRoot, relativePath) {
  const segments = normalizeRepoPath(relativePath).split('/');
  let current = repoRoot;
  for (let index = 0; index < segments.length; index += 1) {
    current = path.join(current, segments[index]);
    try {
      if ((await lstat(current)).isSymbolicLink()) return segments.slice(0, index + 1).join('/');
    } catch (error) {
      if (error.code === 'ENOENT') return null;
      throw error;
    }
  }
  return null;
}

export async function canonicalGitDiff(repoRoot) {
  const info = await gitInfo(repoRoot);
  if (!info.isGit) {
    return {
      ...info,
      canonical: '',
      canonicalBytes: 0,
      diffHash: NON_GIT_BINDING.diffHash,
      changedPaths: [],
      staged: [],
      unstaged: [],
      untracked: [],
      notes: ['Git guarantees unavailable; using explicit degraded sentinel binding']
    };
  }
  const changes = await changedPaths(repoRoot);
  const [staged, unstaged] = await Promise.all([
    diffBytes(repoRoot, ['diff', '--cached', '--binary', '--no-ext-diff', '--src-prefix=a/', '--dst-prefix=b/'], changes.staged),
    diffBytes(repoRoot, ['diff', '--binary', '--no-ext-diff', '--src-prefix=a/', '--dst-prefix=b/'], changes.unstaged)
  ]);
  const chunks = [
    Buffer.from(`base\0${info.baseCommit}\0staged\0`, 'utf8'),
    staged,
    Buffer.from('\0unstaged\0', 'utf8'),
    unstaged,
    Buffer.from('\0untracked\0', 'utf8')
  ];
  const realRoot = await realpath(repoRoot);
  const untracked = [];
  const encodedUntracked = new Set();
  for (const listedRelative of changes.untracked) {
    // Git for Windows can enumerate a junction's descendants without listing the junction itself.
    const linkRoot = await untrackedLinkRoot(repoRoot, listedRelative);
    const relative = linkRoot ?? listedRelative;
    if (encodedUntracked.has(relative)) continue;
    encodedUntracked.add(relative);
    untracked.push(relative);
    const absolute = path.join(repoRoot, relative);
    let content = Buffer.alloc(0);
    let type = 'regular';
    if (linkRoot) {
      type = UNTRACKED_SYMLINK_TYPE;
    } else {
      try {
        const info = await lstat(absolute);
        if (info.isFile()) {
          const physical = await realpath(absolute);
          if (!isPathInside(realRoot, physical)) throw new HarnessError(`Untracked file resolves outside the repository: ${relative}`, 'GIT_PATH_ESCAPE');
          content = await readFile(physical);
        } else {
          type = UNTRACKED_SPECIAL_TYPE;
        }
      } catch (error) {
        if (error.code === 'ENOENT') {
          type = 'missing';
          content = Buffer.alloc(0);
        }
        else throw error;
      }
    }
    chunks.push(Buffer.from(`${Buffer.byteLength(relative)}:${relative}:${type}:${content.length}:`, 'utf8'), content, Buffer.from('\0'));
  }
  const canonicalBuffer = Buffer.concat(chunks);
  const effectiveChangedPaths = [...new Set([...changes.staged, ...changes.unstaged, ...untracked])].sort();
  return {
    ...info,
    canonical: canonicalBuffer.toString('base64'),
    canonicalBytes: canonicalBuffer.length,
    diffHash: sha256(canonicalBuffer),
    changedPaths: effectiveChangedPaths,
    staged: changes.staged,
    unstaged: changes.unstaged,
    untracked,
    notes: []
  };
}

export async function gitFingerprint(repoRoot) {
  const diff = await canonicalGitDiff(repoRoot);
  if (!diff.isGit) return { ...diff, fingerprint: NON_GIT_BINDING.fingerprint };
  const fingerprint = sha256(Buffer.from(`${diff.baseCommit}\0${diff.diffHash}`, 'utf8'));
  return { ...diff, fingerprint };
}

export async function trackedPaths(repoRoot, maxPaths = 100000) {
  const info = await gitInfo(repoRoot);
  if (!info.isGit) return { ...info, paths: [], truncated: false };
  const all = excludeRuntimeState(await nameList(repoRoot, ['ls-files', '-z', '--', '.'])).sort();
  return { ...info, paths: all.slice(0, maxPaths), truncated: all.length > maxPaths, total: all.length };
}

export async function pathStatus(repoRoot) {
  const changes = await changedPaths(repoRoot);
  const map = {};
  for (const item of changes.staged) map[item] = { ...(map[item] ?? {}), index: true };
  for (const item of changes.unstaged) map[item] = { ...(map[item] ?? {}), worktree: true };
  for (const item of changes.untracked) map[item] = { ...(map[item] ?? {}), untracked: true };
  return { ...changes, map };
}
