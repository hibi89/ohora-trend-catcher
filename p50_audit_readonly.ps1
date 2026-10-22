# P50 read-only candidate discovery (no DB open, no copy, no restore).
# Run from the repository root in PowerShell. No Meta API calls.
$ErrorActionPreference = 'Stop'
$inventory = Join-Path $PSScriptRoot 'raw_v2\p50_workspace_inventory.py'
if (-not (Test-Path -LiteralPath $inventory -PathType Leaf)) { throw 'Inventory module missing' }
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw 'LOCALAPPDATA missing; cannot locate P50 safely' }
if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) { throw 'USERPROFILE missing; cannot locate backup safely' }
$local = Join-Path $env:LOCALAPPDATA 'OHORA_P50\p50'
$backup = Join-Path $env:USERPROFILE 'Downloads\OHORA_P50_BACKUP_20261022_173524'
Write-Output 'P50 READ-ONLY CANDIDATE INVENTORY; NOT CANONICAL PROOF'
Write-Output ('Branch: ' + (& git -C $PSScriptRoot branch --show-current))
Write-Output ('HEAD: ' + (& git -C $PSScriptRoot rev-parse HEAD))
Write-Output 'Git working tree (names only):'
& git -C $PSScriptRoot status --short
& python $inventory --candidate $local --candidate $backup
if ($LASTEXITCODE -ne 0) { throw 'Inventory failed; no live action permitted' }
Write-Output 'STOP: no backup restore, quota reset or live collection is authorized.'
