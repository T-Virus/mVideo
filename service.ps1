# MoneyPrinterTurbo local service controller (WebUI)
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'menu')]
    [string]$Action = 'menu'
)

$ErrorActionPreference = 'SilentlyContinue'

$Root     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python   = Join-Path $Root '.venv\Scripts\python.exe'
$UiScript = Join-Path $Root 'webui\Main.py'
$OutLog   = Join-Path $Root 'webui.log'
$ErrLog   = Join-Path $Root 'webui.err.log'
$PidFile  = Join-Path $Root 'webui.pid'
$Port     = 8501
$Url      = "http://127.0.0.1:$Port"

function Get-PidsByPort {
    param([int]$P)
    $found = @()
    $lines = netstat -ano | Select-String ":$P\s"
    foreach ($line in $lines) {
        $text = $line.ToString()
        if ($text -match 'LISTENING') {
            $cols = $text -split '\s+'
            $value = $cols[-1]
            if ($value -match '^\d+$' -and $value -ne '0') {
                $found += [int]$value
            }
        }
    }
    return ($found | Select-Object -Unique)
}

function Start-WebUi {
    if (-not (Test-Path $Python)) {
        Write-Host "ERROR: Python not found at $Python" -ForegroundColor Red
        Write-Host "       Run 'uv sync --frozen' first." -ForegroundColor Red
        return
    }
    $pids = Get-PidsByPort $Port
    if ($pids.Count -gt 0) {
        Write-Host "WebUI is already running (PID: $($pids -join ', '))" -ForegroundColor Yellow
        Write-Host "URL: $Url"
        return
    }
    $arguments = @(
        '-m', 'streamlit', 'run', $UiScript,
        "--server.address=127.0.0.1",
        "--server.port=$Port",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
        "--client.toolbarMode=minimal",
        "--logger.hideWelcomeMessage=true",
        "--server.showEmailPrompt=false"
    )
    $proc = Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $Root `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru
    $proc.Id | Set-Content -Path $PidFile -Encoding ascii
    Write-Host "Starting WebUI (PID: $($proc.Id))..."
    Start-Sleep -Seconds 6
    $check = Get-PidsByPort $Port
    if ($check.Count -gt 0) {
        Write-Host "OK: WebUI is running at $Url" -ForegroundColor Green
    } else {
        Write-Host "WARNING: service may have failed. Check $ErrLog" -ForegroundColor Yellow
    }
}

function Stop-WebUi {
    $targets = @()
    $pids = Get-PidsByPort $Port
    if ($pids) { $targets += $pids }
    if (Test-Path $PidFile) {
        foreach ($line in (Get-Content $PidFile)) {
            if ($line -match '^\d+$') { $targets += [int]$line }
        }
    }
    $targets = $targets | Select-Object -Unique
    if ($targets.Count -eq 0) {
        Write-Host "WebUI is not running." -ForegroundColor Gray
        Remove-Item $PidFile -ErrorAction SilentlyContinue
        return
    }
    foreach ($p in $targets) {
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    Write-Host "Stopped process(es): $($targets -join ', ')" -ForegroundColor Green
}

function Show-Status {
    $pids = Get-PidsByPort $Port
    if ($pids.Count -gt 0) {
        Write-Host "WebUI: RUNNING | PID: $($pids -join ', ') | $Url" -ForegroundColor Green
    } else {
        Write-Host "WebUI: STOPPED" -ForegroundColor Gray
    }
}

function Show-Menu {
    while ($true) {
        Write-Host ""
        Write-Host "===== MoneyPrinterTurbo Service ====="
        Write-Host " 1) Start WebUI"
        Write-Host " 2) Stop WebUI"
        Write-Host " 3) Restart WebUI"
        Write-Host " 4) Status"
        Write-Host " 0) Exit"
        $choice = Read-Host "Select"
        switch ($choice) {
            '1' { Start-WebUi }
            '2' { Stop-WebUi }
            '3' { Stop-WebUi; Start-Sleep -Seconds 2; Start-WebUi }
            '4' { Show-Status }
            '0' { return }
            default { Write-Host "Invalid selection." -ForegroundColor Red }
        }
    }
}

switch ($Action) {
    'start'   { Start-WebUi }
    'stop'    { Stop-WebUi }
    'restart' { Stop-WebUi; Start-Sleep -Seconds 2; Start-WebUi }
    'status'  { Show-Status }
    default   { Show-Menu }
}
