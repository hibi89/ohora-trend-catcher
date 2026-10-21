$ErrorActionPreference = 'Stop'
$python = Join-Path $env:LOCALAPPDATA 'OHORA_PYTHON_RUNTIME\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = 'C:\Users\haein\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$archive = Split-Path -Parent $repo
$officialDataDirectory = -join (0x32,0xcc28,0x20,0xd504,0xb85c,0xc81d,0xd2b8,0x20,0xb370,0xc774,0xd130 | ForEach-Object { [char]$_ })
$roster = Join-Path (Join-Path $archive $officialDataDirectory) 'account_list.csv'
if (-not (Test-Path -LiteralPath $roster -PathType Leaf)) { throw 'Official roster is unavailable.' }
$pilot = Join-Path $env:LOCALAPPDATA 'OHORA_P6_PILOT'
$credentials = 'C:\Users\haein\OneDrive\Desktop\ohora_env_a_b_c'
$state = Join-Path $env:LOCALAPPDATA 'OHORA_P6_PILOT\scheduler_state.json'
Set-Location -LiteralPath $repo
& $python -m raw_v2.three_api_n8n_tick --source $roster --workspace $pilot --credentials-dir $credentials --state-file $state --max-pages 1 --live --confirm-owner-approval
exit $LASTEXITCODE
