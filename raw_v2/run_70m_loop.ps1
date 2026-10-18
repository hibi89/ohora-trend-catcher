# User-started, bounded foreground loop. Does NOT install a Windows scheduled task.
param(
  [Parameter(Mandatory=$true)][string]$Source,
  [Parameter(Mandatory=$true)][string]$EnvFile,
  [string]$OutputDir = '.\raw_v2_results',
  [ValidateRange(1,30)][int]$Cycles = 10,
  [ValidateRange(1,60)][int]$MaxBatches = 1,
  [ValidateRange(1,10)][int]$BatchSize = 5
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path $Source)) { throw 'Official source not found' }
if (-not (Test-Path $EnvFile)) { throw 'Local Meta env file not found' }
$py = Join-Path $PSScriptRoot 'collector.py'
for ($i = 1; $i -le $Cycles; $i++) {
  $start = Get-Date
  Write-Host "OHORA RAW v2 cycle $i of $Cycles start $($start.ToString('s'))"
  & python $py --source $Source --env-file $EnvFile --output-dir $OutputDir `
    --mode balanced --limit 5 --window-days 7 --history-days 365 `
    --cycle-minutes 70 --batch-size $BatchSize --max-batches $MaxBatches --stop-usage 70 --live
  $code = $LASTEXITCODE
  if ($code -notin @(0,2)) { throw "Collector failed with exit code $code. Loop stopped." }
  if ($i -eq $Cycles) { break }
  $seconds = [Math]::Max(0,4200-((Get-Date)-$start).TotalSeconds)
  Write-Host "Cooldown: $([Math]::Ceiling($seconds)) seconds; interrupt PowerShell to stop."
  Start-Sleep -Seconds ([int][Math]::Ceiling($seconds))
}
