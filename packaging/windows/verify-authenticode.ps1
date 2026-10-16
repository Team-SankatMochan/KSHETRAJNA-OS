param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][ValidatePattern('^[0-9A-Fa-f]{40}$')][string]$Thumbprint,
    [Parameter(Mandatory)][string]$SignTool,
    [Parameter(Mandatory)][ValidatePattern('^[0-9A-Fa-f]{64}$')][string]$SignToolSha256
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ((Get-FileHash -LiteralPath $SignTool -Algorithm SHA256).Hash -ne $SignToolSha256) { throw 'Unapproved SignTool binary' }
& $SignTool verify /pa /all /v /tw $Path
if ($LASTEXITCODE -ne 0) { throw 'Authenticode trust verification failed' }
$signature = Get-AuthenticodeSignature -LiteralPath $Path
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $Thumbprint -or
    $null -eq $signature.TimeStamperCertificate) { throw 'Expected trusted signer and timestamp are required' }
Write-Output 'Verified trusted Authenticode signature and timestamp.'
