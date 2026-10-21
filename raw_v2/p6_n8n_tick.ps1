$ErrorActionPreference = 'Stop'
$python = Join-Path $env:LOCALAPPDATA 'OHORA_PYTHON_RUNTIME\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = 'C:\Users\haein\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$archive = Split-Path -Parent $repo
$rosterCandidates = @(Get-ChildItem -LiteralPath $archive -Directory | ForEach-Object {
    $candidate = Join-Path $_.FullName 'account_list.csv'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $candidate }
})
if ($rosterCandidates.Count -ne 1) { throw 'Official roster was not uniquely located.' }
$roster = $rosterCandidates[0]
$pilot = Join-Path $env:LOCALAPPDATA 'OHORA_P6_PILOT'
$credentials = 'C:\Users\haein\OneDrive\Desktop\ohora_env_a_b_c'
$state = Join-Path $env:LOCALAPPDATA 'OHORA_P6_PILOT\scheduler_state.json'
Set-Location -LiteralPath $repo
& $python -m raw_v2.three_api_n8n_tick --source $roster --workspace $pilot --credentials-dir $credentials --state-file $state --max-pages 1 --live --confirm-owner-approval
exit $LASTEXITCODE
