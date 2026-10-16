param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][ValidatePattern('^[0-9A-Fa-f]{40}$')][string]$Thumbprint,
    [Parameter(Mandatory)][ValidatePattern('^https://[^\s]+$')][string]$TimestampUrl,
    [Parameter(Mandatory)][string]$SignTool,
    [Parameter(Mandatory)][ValidatePattern('^[0-9A-Fa-f]{64}$')][string]$SignToolSha256
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:KSHETRAJNA_PROTECTED_SIGNING -ne 'verified') {
    throw 'Signing is restricted to the protected CI environment'
}
if ([IO.Path]::GetExtension($Path) -ne '.msi') { throw 'Only an MSI can be final-signed' }
if ((Get-FileHash -LiteralPath $SignTool -Algorithm SHA256).Hash -ne $SignToolSha256) { throw 'Unapproved SignTool binary' }
$certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$Thumbprint"
if (-not $certificate.HasPrivateKey -or $certificate.NotAfter -lt (Get-Date)) { throw 'Provisioned signing identity unavailable' }
# The identity is pre-provisioned by the release operator; no PFX import, account or store modification.
& $SignTool sign /s My /sha1 $Thumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $Path
if ($LASTEXITCODE -ne 0) { throw 'Signing failed; no unsigned final-release fallback' }
& "$PSScriptRoot/verify-authenticode.ps1" -Path $Path -Thumbprint $Thumbprint -SignTool $SignTool -SignToolSha256 $SignToolSha256
