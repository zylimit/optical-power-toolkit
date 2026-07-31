#!/usr/bin/env pwsh
# Usage: pwsh .agents/scripts/fast-mode.ps1 on [hours] | off | status
param(
    [Parameter(Position = 0)]
    [string]$Action = 'status',
    [Parameter(Position = 1)]
    [string]$Hours = '24'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$runtime = Join-Path $repoRoot '.codex/runtime/harness.mjs'

& node $runtime fast $Action --hours $Hours --root $repoRoot
exit $LASTEXITCODE
