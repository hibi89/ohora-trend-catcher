$ErrorActionPreference = 'Stop'
$python = Join-Path $env:LOCALAPPDATA 'OHORA_PYTHON_RUNTIME\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = 'C:\Users\haein\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$engineRoot = Join-Path $repo '.tmp\p50_engine'
$expectedEngineCommit = '8548ecb650bb17a7f671829b740609f6a72cac5a'
$engineCommit = (& git -C $engineRoot rev-parse HEAD 2>$null | Out-String).Trim()
if ($engineCommit -cne $expectedEngineCommit -or
        -not (Test-Path -LiteralPath (Join-Path $engineRoot 'raw_v2\three_api_p50_manual_pilot.py') -PathType Leaf)) {
    throw 'Pinned P50 engine revision is unavailable.'
}
$env:OHORA_P50_ENGINE_ROOT = $engineRoot
$roster = $env:OHORA_P50_ROSTER
if (-not $roster -or -not (Test-Path -LiteralPath $roster -PathType Leaf)) {
    throw 'P50 roster is unavailable.'
}
$workspace = Join-Path $env:LOCALAPPDATA 'OHORA_P50\p50'
$credentials = $env:OHORA_P50_CREDENTIALS_DIR
if ($env:OHORA_P50_LIVE_APPROVED -ceq 'YES' -and -not $credentials) {
    throw 'P50 credential directory is not configured.'
}
Set-Location -LiteralPath $repo
$arguments = @('-m', 'raw_v2.three_api_p50_n8n_tick', '--source', $roster,
    '--workspace', $workspace)
if ($env:OHORA_P50_LIVE_APPROVED -ceq 'YES') {
    $arguments += @('--credentials-dir', $credentials, '--live',
        '--confirm-owner-approval')
}
& $python @arguments
exit $LASTEXITCODE
