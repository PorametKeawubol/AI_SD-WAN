param([switch]$KeepAliveOnly)
$ErrorActionPreference = 'Stop'
$labRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $labRoot '.work/wsl-lab-keepalive.json'
$labKeeper = $null
if (Test-Path -LiteralPath $statePath) {
    $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $($saved.pid)"
    if ($candidate -and $candidate.Name -eq 'wsl.exe' -and $candidate.CommandLine -match '--distribution AI-SDWAN-Lab' -and $candidate.CommandLine -match 'tail -f /dev/null') {
        $labKeeper = Get-Process -Id $saved.pid
    }
}
if (-not $labKeeper) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $statePath) -Force | Out-Null
    $labKeeper = Start-Process -FilePath "$env:WINDIR\System32\wsl.exe" -ArgumentList @('--distribution', 'AI-SDWAN-Lab', '--user', 'root', '--exec', 'tail', '-f', '/dev/null') -WindowStyle Hidden -PassThru
    @{distribution='AI-SDWAN-Lab'; pid=$labKeeper.Id; purpose='Keep the isolated lab services running'; started_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
}
Write-Output "Lab keepalive PID: $($labKeeper.Id)"
if (-not $KeepAliveOnly) {
    & wsl.exe --distribution AI-SDWAN-Lab --user root --exec systemctl start docker libvirtd
    if ($LASTEXITCODE -ne 0) { throw 'Linux host services did not start.' }
    & wsl.exe --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/configure_lab_docker.py --apply
    if ($LASTEXITCODE -ne 0) { throw 'The dedicated native lab Docker endpoint did not verify.' }
    & wsl.exe --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python -m lab.resume --apply
    if ($LASTEXITCODE -ne 0) { throw 'Lab resume stopped; existing resources were preserved for inspection.' }
    if (Test-Path -LiteralPath (Join-Path $labRoot 'lab/runtime/official-gui-login.json')) {
        # Guests may still be booting when resume returns.
        for ($guiAttempt = 0; $guiAttempt -lt 6; $guiAttempt++) {
            & wsl.exe --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/expose_official_edge_ui.py
            if ($LASTEXITCODE -eq 0) { break }
            if ($guiAttempt -lt 5) { Start-Sleep -Seconds 5 }
        }
        if ($LASTEXITCODE -ne 0) { throw 'The official flexiEdge GUI relay did not verify.' }
    }
    if (Test-Path -LiteralPath (Join-Path $labRoot 'lab/runtime/community-gui/source.zip')) {
        & wsl.exe --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/serve_community_gui.py --background
        if ($LASTEXITCODE -ne 0) { throw 'The community GUI trial did not verify.' }
    }
}
