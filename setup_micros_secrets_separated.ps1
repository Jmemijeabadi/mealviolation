param(
    [string]$RepoPath = "C:\Users\Jordan Memije\Documents\GitHub\mealviolation"
)

$ErrorActionPreference = "Stop"

function Read-HiddenValue {
    param([string]$Prompt)
    $secure = Read-Host $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Escape-Toml {
    param([string]$Value)
    if ($Value.Contains("`r") -or $Value.Contains("`n")) {
        throw "La credencial contiene saltos de línea."
    }
    return '"' + $Value.Replace("\", "\\").Replace('"', '\"') + '"'
}

if (-not (Test-Path $RepoPath)) {
    throw "No existe el repositorio: $RepoPath"
}

Set-Location $RepoPath
$streamlit = Join-Path $RepoPath ".streamlit"
New-Item -ItemType Directory -Force -Path $streamlit | Out-Null

Write-Host ""
Write-Host "BI API — credenciales independientes" -ForegroundColor Cyan
$biClientId = Read-Host "BI Client ID"
$biUsername = Read-Host "BI API username"
$biPassword = Read-HiddenValue "BI API password"

$biToml = @"
[oracle_bi]
auth_server = "https://ors-idm.mtu5.oraclerestaurants.com"
application_server = "https://simphony-home.mtu5.oraclerestaurants.com"
org_identifier = "BYC"

client_id = $(Escape-Toml $biClientId)
username = $(Escape-Toml $biUsername)
password = $(Escape-Toml $biPassword)

application_name = "Meal Compliance Dashboard"
timeout_seconds = 45
verify_ssl = true

[oracle_ccapi]
base_url = ""
"@

Write-Host ""
Write-Host "Labor Management — credenciales independientes" -ForegroundColor Cyan
$laborToken = Read-HiddenValue "Labor API token"
$laborPassword = Read-HiddenValue "Labor API password"

$laborToml = @"
[oracle_labor]
soap_url = "https://mtu5-ohra.oracleindustry.com/ws/mylabor"
wsdl_url = "https://mtu5-ohra.oracleindustry.com/ws/mylabor?wsdl"
rest_base_url = "https://mtu5-ohra.oracleindustry.com/rest/services"

api_token = $(Escape-Toml $laborToken)
password = $(Escape-Toml $laborPassword)

timeout_seconds = 45
verify_ssl = true
"@

$biPath = Join-Path $streamlit "bi_secrets.toml"
$laborPath = Join-Path $streamlit "labor_secrets.toml"

Set-Content -Path $biPath -Value $biToml -Encoding UTF8
Set-Content -Path $laborPath -Value $laborToml -Encoding UTF8

$gitignore = Join-Path $RepoPath ".gitignore"
if (-not (Test-Path $gitignore)) {
    New-Item -ItemType File -Path $gitignore | Out-Null
}

$ignoreLines = @(
    ".streamlit/bi_secrets.toml",
    ".streamlit/labor_secrets.toml",
    "micros_full_capability_report.json"
)

$current = Get-Content $gitignore -ErrorAction SilentlyContinue
foreach ($line in $ignoreLines) {
    if ($current -notcontains $line) {
        Add-Content -Path $gitignore -Value $line
    }
}

python -c "import tomllib; [tomllib.load(open(p,'rb')) for p in [r'$biPath',r'$laborPath']]; print('Ambos TOML son válidos')"
if ($LASTEXITCODE -ne 0) {
    throw "No se pudo validar la sintaxis TOML."
}

Write-Host ""
Write-Host "Archivos creados:" -ForegroundColor Green
Write-Host "  $biPath"
Write-Host "  $laborPath"
