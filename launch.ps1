# Bybit Spot AI Screener Web Launcher & Health Guard
$port = 5600
$url = "http://localhost:$port/"
$pulseUrl = "http://localhost:$port/api/pulse"
$webDir = $PSScriptRoot
$serverScript = Join-Path $webDir "run_server.ps1"

function Test-ServerHttp {
    try {
        $req = [System.Net.HttpWebRequest]::Create($pulseUrl)
        $req.Timeout = 1200
        $req.ReadWriteTimeout = 1200
        $req.Proxy = $null
        $res = $req.GetResponse()
        $res.Close()
        return $true
    } catch {
        return $false
    }
}

$isAlive = Test-ServerHttp

if (-not $isAlive) {
    Write-Host "[*] Starting Bybit Screener Web Server in background..." -ForegroundColor Cyan
    
    # Clean any stale process on port 5600
    try {
        $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            if ($c.OwningProcess -gt 4) {
                Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {}

    Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$serverScript`"" -WorkingDirectory $webDir
    
    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Milliseconds 200
        if (Test-ServerHttp) { break }
    }
}

Write-Host "[OK] Opening Screener Web UI in browser: $url" -ForegroundColor Green
Start-Process $url
Start-Sleep -Seconds 1
