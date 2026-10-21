$ErrorActionPreference = 'Stop'
$python = Join-Path $env:LOCALAPPDATA 'OHORA_PYTHON_RUNTIME\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = 'C:\Users\haein\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
$repo = 'C:\Users\haein\OneDrive\Desktop\2차 프로젝트 아카이브\ohora-trend-catcher'
$roster = 'C:\Users\haein\OneDrive\Desktop\2차 프로젝트 아카이브\2차 프로젝트 데이터\account_list.csv'
$pilot = Join-Path $env:LOCALAPPDATA 'OHORA_P6_PILOT'
$credentials = 'C:\Users\haein\OneDrive\Desktop\ohora_env_a_b_c'
$state = Join-Path $env:LOCALAPPDATA 'OHORA_P6_PILOT\scheduler_state.json'
Set-Location -LiteralPath $repo
& $python -m raw_v2.three_api_n8n_tick --source $roster --workspace $pilot --credentials-dir $credentials --state-file $state --max-pages 1 --live --confirm-owner-approval
exit $LASTEXITCODE
