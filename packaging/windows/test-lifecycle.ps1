param(
    [Parameter(Mandatory)][string]$Msi,
    [Parameter(Mandatory)][string]$UpgradeMsi,
    [Parameter(Mandatory)][ValidateSet('x64', 'arm64')][string]$Architecture,
    [Parameter(Mandatory)][string]$ExpectedCommit,
    [Parameter(Mandatory)][string]$Report,
    [Parameter(Mandatory)][switch]$DisposableRunner
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# This test deliberately installs/uninstalls. Never invoke it on a personal machine.
if (-not $DisposableRunner -or $env:GITHUB_ACTIONS -ne 'true' -or $env:KSHETRAJNA_DISPOSABLE_VM -ne 'approved') {
    throw 'Lifecycle test requires an explicitly provisioned disposable CI VM'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run lifecycle qualification as a standard user, never elevated'
}
$installed = Join-Path $env:LOCALAPPDATA "Programs/Kshetrajna/$Architecture"
$Msi = (Resolve-Path -LiteralPath $Msi).Path
$UpgradeMsi = (Resolve-Path -LiteralPath $UpgradeMsi).Path
foreach ($path in @($Msi, $UpgradeMsi)) {
    & "$PSScriptRoot/verify-authenticode.ps1" -Path $path -Thumbprint $env:SIGNING_THUMBPRINT -SignTool $env:SIGNTOOL_PATH -SignToolSha256 $env:SIGNTOOL_SHA256
}
function Check-Products([string]$Phase) {
    $result = & python "$PSScriptRoot/../../scripts/check_msi_upgrade.py" --predecessor $Msi --candidate $UpgradeMsi --architecture $Architecture --phase $Phase
    if ($LASTEXITCODE -ne 0) { throw "MSI identity/registration check failed: $Phase" }
    return ($result | ConvertFrom-Json)
}
# Read-only preflight checks SummaryInformation architecture, exact UpgradeCode
# family, distinct ProductCodes, increasing version, and a clean registration state.
$products = Check-Products 'clean'
$history = Join-Path $env:LOCALAPPDATA 'Kshetrajna'
if ((Test-Path -LiteralPath $installed) -or (Test-Path -LiteralPath $history)) { throw 'Test account must be clean' }
New-Item -ItemType Directory -Path $history | Out-Null
$sentinel = Join-Path $history 'release-lifecycle-sentinel.txt'
[IO.File]::WriteAllText($sentinel, 'preserve-test-history')
function Run-Msi([string[]]$Arguments) {
    $p = Start-Process msiexec.exe -ArgumentList $Arguments -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "MSI failed or required restart: $($p.ExitCode)" }
}
try {
    Run-Msi @('/i', "`"$Msi`"", '/qn', '/norestart', 'ALLUSERS=', 'MSIINSTALLPERUSER=1')
    Check-Products 'predecessor' | Out-Null
    $before = Get-Content -Raw -LiteralPath (Join-Path $installed 'bundle.json') | ConvertFrom-Json
    if ($before.architecture -ne $Architecture -or [version]$before.version -ne [version]$products.predecessor.version) { throw 'Predecessor payload identity mismatch' }
    Run-Msi @('/i', "`"$UpgradeMsi`"", '/qn', '/norestart', 'ALLUSERS=', 'MSIINSTALLPERUSER=1')
    Check-Products 'upgraded' | Out-Null
    $after = Get-Content -Raw -LiteralPath (Join-Path $installed 'bundle.json') | ConvertFrom-Json
    if ($after.source_commit -ne $ExpectedCommit -or [version]$after.version -le [version]$before.version) { throw 'Upgrade identity/version mismatch' }
    if ($after.architecture -ne $Architecture -or [version]$after.version -ne [version]$products.candidate.version) { throw 'Candidate payload identity mismatch' }
    & python "$PSScriptRoot/../../scripts/smoke_windows_bundle.py" --bundle $installed --architecture $Architecture
    if ($LASTEXITCODE -ne 0) { throw 'Installed MSI runtime smoke failed' }
    Run-Msi @('/x', "`"$UpgradeMsi`"", '/qn', '/norestart')
    Check-Products 'uninstalled' | Out-Null
    if (Test-Path -LiteralPath (Join-Path $installed 'runtime/python.exe')) { throw 'Uninstall left executable payload' }
    if ([IO.File]::ReadAllText($sentinel) -ne 'preserve-test-history') { throw 'History was changed by lifecycle' }
    @{ schema = 1; source_commit = $ExpectedCommit; architecture = $Architecture;
       status = 'passed'; standard_user = $true; install = 'passed'; upgrade = 'passed'; uninstall = 'passed';
       history_preserved = $true; msi_sha256 = (Get-FileHash -LiteralPath $UpgradeMsi -Algorithm SHA256).Hash.ToLowerInvariant()
    } | ConvertTo-Json | Set-Content -Encoding utf8 -LiteralPath $Report
} finally {
    # No recursive cleanup of user paths. Disposable runner teardown owns remaining fixtures.
    if (Test-Path -LiteralPath $sentinel) { Remove-Item -LiteralPath $sentinel }
}
