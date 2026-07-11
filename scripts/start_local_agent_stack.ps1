param(
    [int]$Port = 8080,
    [int]$CdpPort = 9222,
    [switch]$Headless,
    [string]$CdpUserDataDir = "browser_data\xhs_cdp_profile",
    [string]$StartUrl = "https://www.xiaohongshu.com/explore",
    [string]$AgentToken = "",
    [string]$UvProjectEnvironment = ".tmp\uv-agent-venv"
)

$ErrorActionPreference = "Stop"

function Wait-Health {
    param([string]$Url, [int]$TimeoutSeconds = 45)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $result = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 3
            if ($result.status -eq "ok") {
                return $true
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

$baseUrl = "http://127.0.0.1:$Port"
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1

if (-not $listener) {
    New-Item -ItemType Directory -Force -Path ".tmp" | Out-Null
    $env:UV_PROJECT_ENVIRONMENT = $UvProjectEnvironment
    if ($AgentToken) {
        $env:MEDIACRAWLER_AGENT_TOKEN = $AgentToken
    }
    $out = ".tmp\local-agent-$Port.out.log"
    $err = ".tmp\local-agent-$Port.err.log"
    $args = @("run", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "$Port")
    $process = Start-Process -FilePath "uv" -ArgumentList $args -WorkingDirectory (Get-Location) `
        -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -PassThru
    Write-Host "Started MediaCrawler API on $baseUrl (pid=$($process.Id))"
} else {
    Write-Host "MediaCrawler API already listening on $baseUrl (pid=$($listener.OwningProcess))"
}

if (-not (Wait-Health -Url $baseUrl)) {
    throw "MediaCrawler API did not become healthy at $baseUrl"
}

$body = @{
    port = $CdpPort
    headless = [bool]$Headless
    user_data_dir = $CdpUserDataDir
    start_url = $StartUrl
} | ConvertTo-Json

$cdp = Invoke-RestMethod -Uri "$baseUrl/api/browser/cdp/start" -Method Post `
    -ContentType "application/json" -Body $body -TimeoutSec 60

Write-Host "CDP status: $($cdp.status) port=$($cdp.port)"
Write-Host "CDP profile: $($cdp.user_data_dir)"
if ($StartUrl) {
    Write-Host "Opened: $StartUrl"
}
Write-Host ""
Write-Host "Hermes/WSL base URL:"
Write-Host "  $baseUrl"
Write-Host ""
Write-Host "Hermes MCP profile:"
Write-Host "  MEDIACRAWLER_MCP_TOOL_PROFILE=desktop_agent"
if ($AgentToken) {
    Write-Host "  MEDIACRAWLER_AGENT_TOKEN=<the token you passed>"
}
