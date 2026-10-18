param(
  [string]$LanIp = "",
  [switch]$InstallWindowsCA
)

$ErrorActionPreference = "Stop"
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$certificateDir = Join-Path $workspaceRoot ".certs"
$openssl = (Get-Command openssl -ErrorAction Stop).Source
$opensslRoot = Split-Path (Split-Path $openssl -Parent) -Parent
$opensslConfig = Join-Path $opensslRoot "ssl\openssl.cnf"
$configArguments = if (Test-Path -LiteralPath $opensslConfig) { @("-config", $opensslConfig) } else { @() }

if (-not $LanIp) {
  $defaultRoute = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" `
    -ErrorAction Stop | Sort-Object RouteMetric, InterfaceMetric | Select-Object -First 1
  $LanIp = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $defaultRoute.InterfaceIndex `
    -ErrorAction Stop | Where-Object { $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1 -ExpandProperty IPAddress
}

if (-not [System.Net.IPAddress]::TryParse($LanIp, [ref]([System.Net.IPAddress]$null))) {
  throw "Invalid LAN IP address: $LanIp"
}

New-Item -ItemType Directory -Path $certificateDir -Force | Out-Null
$caKey = Join-Path $certificateDir "dev-ca.key"
$caCertificate = Join-Path $certificateDir "dev-ca.crt"
$serverKey = Join-Path $certificateDir "dev-server.key"
$serverRequest = Join-Path $certificateDir "dev-server.csr"
$serverCertificate = Join-Path $certificateDir "dev-server.crt"
$extensions = Join-Path $certificateDir "dev-server.ext"

if (-not (Test-Path -LiteralPath $caKey) -or -not (Test-Path -LiteralPath $caCertificate)) {
  & $openssl req @configArguments -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes `
    -keyout $caKey -out $caCertificate -subj "/CN=Medical Agent Development CA"
  if ($LASTEXITCODE -ne 0) { throw "Failed to create development CA." }
}

$extensionContent = @"
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,IP:127.0.0.1,IP:$LanIp
"@
Set-Content -LiteralPath $extensions -Value $extensionContent -Encoding ascii

& $openssl req @configArguments -newkey rsa:2048 -nodes -keyout $serverKey -out $serverRequest `
  -subj "/CN=$LanIp"
if ($LASTEXITCODE -ne 0) { throw "Failed to create server certificate request." }

& $openssl x509 -req -in $serverRequest -CA $caCertificate -CAkey $caKey -CAcreateserial `
  -out $serverCertificate -days 825 -sha256 -extfile $extensions
if ($LASTEXITCODE -ne 0) { throw "Failed to sign server certificate." }

$envLocal = @"
VITE_HTTPS_CERT_PATH=$($serverCertificate.Replace('\', '/'))
VITE_HTTPS_KEY_PATH=$($serverKey.Replace('\', '/'))
"@
Set-Content -LiteralPath (Join-Path $workspaceRoot ".env.local") -Value $envLocal -Encoding utf8

if ($InstallWindowsCA) {
  Import-Certificate -FilePath $caCertificate -CertStoreLocation "Cert:\CurrentUser\Root" | Out-Null
}

Write-Host "HTTPS development certificate created for $LanIp"
Write-Host "Phone CA certificate: $caCertificate"
Write-Host "After trusting the CA on the phone, open: https://${LanIp}:5173"
