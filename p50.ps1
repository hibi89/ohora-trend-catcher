param(
    [Parameter(Position=0, Mandatory=$true)]
    [ValidateSet('history','latest','status')]
    [string] $Mode,
    [switch] $Live,
    [int] $MaxPages = 0,
    [string] $Source = "$env:USERPROFILE\Downloads\ohora_p50_roster_20261022.csv",
    [string] $Workspace = "$env:LOCALAPPDATA\OHORA_P50\p50",
    [string] $CredentialsDir = "$env:USERPROFILE\OneDrive\Desktop\ohora_env_a_b_c"
)
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'raw_v2\three_api_p50_cycle.py'
if (!(Test-Path -LiteralPath $runner)) { throw 'P50 runner not found' }
if ($MaxPages -lt 0) { throw 'MaxPages must be a positive integer; no HTTP was attempted.' }
if ($Mode -eq 'status' -and $PSBoundParameters.ContainsKey('MaxPages')) { throw 'Status cannot accept MaxPages.' }
if ($Mode -ne 'status' -and $PSBoundParameters.ContainsKey('MaxPages') -and $MaxPages -eq 0) { throw 'Explicit MaxPages must be positive; no HTTP was attempted.' }
$argsList = @($runner, $Mode, '--source', $Source, '--workspace', $Workspace)
if ($Mode -ne 'status') {
    if (!$Live) { throw 'Live collection requires operator -Live switch; no HTTP was attempted.' }
    $argsList += @('--credentials-dir', $CredentialsDir, '--live', '--confirm-owner-approval')
    if ($MaxPages -gt 0) { $argsList += @('--max-pages', "$MaxPages") }
} elseif ($Live) {
    throw 'Status must remain read-only'
}
& python @argsList
exit $LASTEXITCODE
