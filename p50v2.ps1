param(
    [switch]$Live,
    [ValidateRange(1,3000)][int]$MaxRequests = 1,
    [string]$Source = "$env:USERPROFILE\Downloads\ohora_p50_roster_20261022.csv",
    [string]$Workspace = "$env:LOCALAPPDATA\OHORA_P50_V2\p50_v2",
    [string]$CredentialsDir = "$env:USERPROFILE\OneDrive\Desktop\ohora_env_a_b_c",
    [Nullable[int]]$UsageA,
    [Nullable[int]]$UsageB,
    [Nullable[int]]$UsageC
)
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'raw_v2\p50_v2_collector.py'
if (!(Test-Path -LiteralPath $Source)) { throw 'P50 CSV not found' }
if (!(Test-Path -LiteralPath $runner)) { throw 'P50 v2 runner not found' }
$argsList = @($runner,'--source',$Source,'--workspace',$Workspace,
              '--credentials-dir',$CredentialsDir,'--max-requests',"$MaxRequests")
if ($Live) {
    if ($null -eq $UsageA -or $null -eq $UsageB -or $null -eq $UsageC) {
        throw 'Verified current A/B/C usage values required for live collection'
    }
    $argsList += @('--live','--confirm-owner-approval',
        '--verified-usage-a',"$UsageA",
        '--verified-usage-b',"$UsageB",
        '--verified-usage-c',"$UsageC")
}
& python @argsList
exit $LASTEXITCODE
