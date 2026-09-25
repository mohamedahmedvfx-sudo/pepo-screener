# Bybit Spot AI Screener Web Launcher & Health Guard
$port = 5600
$url = "http://localhost:$port/"
$webDir = $PSScriptRoot
$statusUrl = "http://localhost:$port/api/pulse"

function Test-ServerHttp {
    try {
        $req = [System.Net.HttpWebRequest]::Create($statusUrl)
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
    Write-Host "[*] Checking port $port and cleaning up stale instances..." -ForegroundColor DarkGray
    
    # 1. Kill any stale process listening on port 5600
    try {
        $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            if ($c.OwningProcess -gt 4) {
                Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {}

    Start-Sleep -Milliseconds 300

    Write-Host "[*] Starting Bybit Screener Web Server in background..." -ForegroundColor Cyan
    Start-Process py -ArgumentList "server.py" -WorkingDirectory $webDir -WindowStyle Hidden

    # 2. Wait up to 5 seconds for server to answer HTTP
    $ready = $false
    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Milliseconds 200
        if (Test-ServerHttp) {
            $ready = $true
            break
        }
    }

    if ($ready) {
        Write-Host "[OK] Server started and verified successfully!" -ForegroundColor Green
    } else {
        Write-Host "[!] Server starting... launching browser." -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK] Server is already active and responsive!" -ForegroundColor Green
}

# Open browser
Start-Process $url
Start-Sleep -Seconds 1
