$ErrorActionPreference = 'Stop'
$python = Join-Path $env:LOCALAPPDATA 'OHORA_PYTHON_RUNTIME\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = 'C:\Users\haein\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
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
