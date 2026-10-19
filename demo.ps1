#Requires -Version 5.1
<#
.SYNOPSIS
Short commands for the prepared AI SD-WAN lab; run .\demo.ps1 help.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'start', 'open', 'status', 'traffic', 'natural', 'down',
        'fault', 'reset', 'stop', 'auto', 'manual', 'profile', 'approve', 'reject',
        'ping', 'logs', 'scenarios', 'plan', 'benchmark')]
    [string]$Action = 'help',
    [Parameter(Position = 1)][string]$Target = '',
    [ValidateRange(0, 7200)][int]$Seconds = 0,
    [ValidateSet('auto', 'manual')][string]$Mode = 'auto',
    [ValidateSet('voice', 'interactive', 'bulk')][string]$Profile = 'voice',
    [ValidateRange(1, 100)][int]$Repetitions = 3,
    [switch]$NoML,
    [switch]$NoOpen,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$Action = $Action.ToLowerInvariant()
$Target = $Target.ToLowerInvariant()
$Mode = $Mode.ToLowerInvariant()
$Profile = $Profile.ToLowerInvariant()
$demoRoot = $PSScriptRoot
$demoApi = 'https://localhost:8000'
$demoHeaders = $null
$demoProfileSpecified = $PSBoundParameters.ContainsKey('Profile')
$demoSecondsSpecified = $PSBoundParameters.ContainsKey('Seconds')

function Show-DemoHelp {
    @'
AI SD-WAN demo (Windows PowerShell, from this repository)

  .\demo.ps1 start                 Start lab + experimental ML + Auto/Voice; open Grafana
  .\demo.ps1 start -Mode manual    Start in Manual for approval demo
  .\demo.ps1 start -NoML -NoOpen   Start reactive API without opening a browser
  .\demo.ps1 status                Read live state and workload status (-Json for raw data)
  .\demo.ps1 open                  Open Grafana, API docs and the existing GUIs
  .\demo.ps1 traffic               Real traffic: 60s baseline, 90s busy, 60s recovery
  .\demo.ps1 traffic -Seconds 0    Repeat real traffic until reset/stop
  .\demo.ps1 natural               Natural WAN profiles for 180 seconds
  .\demo.ps1 down                  Down the currently configured WAN; Enter restores
  .\demo.ps1 down wan1             Down WAN1; Enter restores (wan2/wan3 also supported)
  .\demo.ps1 down wan1 -Seconds 30 Restore automatically after 30 seconds
  .\demo.ps1 fault S08             WAN1 latency fault; Enter restores (S01..S10)
  .\demo.ps1 ping                  Continuous end-to-end ping; Ctrl+C stops ping
  .\demo.ps1 manual                Manual mode, preserving the current profile
  .\demo.ps1 approve               Approve the current pending recommendation
  .\demo.ps1 reject                Reject the current pending recommendation
  .\demo.ps1 auto                  Auto mode, preserving the current profile
  .\demo.ps1 profile bulk          Change profile (voice / interactive / bulk)
  .\demo.ps1 reset                 Stop loads, restore all WANs, leave API in Manual
  .\demo.ps1 stop                  Reset, then stop API; keep VMs/controller/Grafana
  .\demo.ps1 logs                  Recent API and workload logs + decision events
  .\demo.ps1 scenarios             Show the full balanced catalogue (38 cases)
  .\demo.ps1 plan traffic          Show workload plan only (natural / S01..S10 also work)
  .\demo.ps1 benchmark             S01, four strategies x 3 runs; stops the live API

Run start first. Other actions do not implicitly start the lab or enable Auto.
Down/fault preserve Manual/Auto. Use a second terminal or Grafana to approve
while the fault command waits. Do not close that terminal before restoration.
ML remains experimental; a WAN Down demonstration alone does not prove ML gain.
'@
}

function Invoke-DemoWsl {
    param([string[]]$Arguments)
    & wsl.exe --distribution AI-SDWAN-Lab --user root `
        --cd /opt/ai-sdwan-lab @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Lab command failed; see output above.' }
}

function Invoke-LabPython {
    Invoke-DemoWsl -Arguments (@('--exec', '.venv/bin/python') + @($args))
}

function Get-DemoWorkload {
    param([string]$Module)
    $demoWorkloadText = @(Invoke-LabPython -m $Module status) -join "`n"
    $demoWorkloadText | ConvertFrom-Json
}

function Invoke-DemoApi {
    param([string]$Path, [string]$Method = 'Get', [hashtable]$Body,
        [switch]$Public, [int]$TimeoutSeconds = 15)
    $demoRequest = @{ Uri = "$demoApi$Path"; Method = $Method; TimeoutSec = $TimeoutSeconds }
    if (-not $Public) {
        if (-not $demoHeaders) {
            $demoTokenPath = Join-Path $demoRoot 'lab/runtime/observability/api-token.txt'
            if (-not (Test-Path -LiteralPath $demoTokenPath)) {
                throw 'API token missing. Use the prepared private runtime described in README.'
            }
            $demoToken = (Get-Content -LiteralPath $demoTokenPath -Raw).Trim()
            if (-not $demoToken) { throw 'API token file is empty.' }
            $script:demoHeaders = @{ Authorization = "Bearer $demoToken" }
        }
        $demoRequest.Headers = $demoHeaders
    }
    if ($null -ne $Body) {
        $demoRequest.ContentType = 'application/json'
        $demoRequest.Body = $Body | ConvertTo-Json -Compress
    }
    Invoke-RestMethod @demoRequest
}

function Get-DemoState {
    Invoke-DemoApi -Path '/api/state'
}

function Wait-DemoIdle {
    for ($demoAttempt = 0; $demoAttempt -lt 45; $demoAttempt++) {
        $demoState = Get-DemoState
        if (-not $demoState.apply_inflight) { return $demoState }
        Start-Sleep -Seconds 2
    }
    throw 'Policy apply is still in flight. Inspect status/logs; no force kill was sent.'
}

function Wait-DemoBaseline {
    Write-Host 'Waiting for all three WANs to be fresh/up and policy apply to finish...'
    for ($demoAttempt = 0; $demoAttempt -lt 30; $demoAttempt++) {
        $demoState = Get-DemoState
        if ($demoState.backend -ne 'flexiwan' -or $demoState.observing_native_qbr) {
            throw 'This action requires the live flexiWAN control API, not replay/native observation.'
        }
        $demoReady = -not $demoState.apply_inflight
        foreach ($demoWan in @('wan1', 'wan2', 'wan3')) {
            $demoSample = $demoState.telemetry.$demoWan
            if (-not $demoSample -or -not $demoSample.fresh -or -not $demoSample.up) {
                $demoReady = $false
            }
        }
        if ($demoReady) { return $demoState }
        Start-Sleep -Seconds 2
    }
    throw 'WAN baseline is not ready. Inspect status/logs before starting another demo.'
}

function Stop-DemoWanLoads {
    Invoke-LabPython -m lab.real_traffic stop | Out-Host
    $demoStopped = $false
    for ($demoAttempt = 0; $demoAttempt -lt 30; $demoAttempt++) {
        $demoWorkload = Get-DemoWorkload -Module 'lab.real_traffic'
        if (-not $demoWorkload.active -and -not $demoWorkload.starting) {
            if ($demoWorkload.cleanup_errors) { throw 'Real traffic cleanup failed. Inspect logs.' }
            if ($demoWorkload.PSObject.Properties.Name -contains 'restored_baseline') {
                if (-not $demoWorkload.restored_baseline) { throw 'Real traffic baseline not restored.' }
            }
            $demoStopped = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $demoStopped) { throw 'Real traffic is still stopping. Inspect status/logs.' }

    Invoke-LabPython -m lab.natural_wan stop | Out-Host
    $demoNatural = Get-DemoWorkload -Module 'lab.natural_wan'
    if ($demoNatural.active) { throw 'Natural WAN is still active.' }
    if ($demoNatural.PSObject.Properties.Name -contains 'restored_baseline') {
        if (-not $demoNatural.restored_baseline) { throw 'Natural WAN baseline not restored.' }
    }
}

function Stop-DemoApi {
    & wsl.exe --distribution AI-SDWAN-Lab --user root `
        --cd /opt/ai-sdwan-lab --exec test -f lab/runtime/live-dashboard.pid
    if ($LASTEXITCODE -eq 0) {
        if (Test-DemoApi) {
            Invoke-DemoApi -Path '/api/configuration' -Method Post -Body @{ mode = 'manual' } | Out-Null
            Wait-DemoIdle | Out-Null
        }
        Invoke-LabPython scripts/serve_live_lab.py --stop | Out-Host
    } elseif ($LASTEXITCODE -ne 1) {
        throw 'Could not check the existing API PID file.'
    }
}

function Test-DemoApi {
    try {
        $demoHealth = Invoke-DemoApi -Path '/health' -Public -TimeoutSeconds 5
        return $demoHealth.status -eq 'ok'
    } catch { return $false }
}

function Open-DemoDashboard {
    Start-Process 'https://localhost:3000/d/ai-sdwan?var-stream=__latest_live__&from=now-15m&to=now'
}

function Show-DemoStatus {
    $demoHealth = Invoke-DemoApi -Path '/health' -Public -TimeoutSeconds 5
    $demoState = Get-DemoState
    $demoTraffic = Get-DemoWorkload -Module 'lab.real_traffic'
    $demoNatural = Get-DemoWorkload -Module 'lab.natural_wan'
    if ($Json) {
        @{ health = $demoHealth; state = $demoState;
            real_traffic = $demoTraffic; natural_wan = $demoNatural } | ConvertTo-Json -Depth 12
        return
    }
    $demoState | Select-Object mode, profile, confirmed_path, model_loaded,
        network_available, apply_inflight, pending | Format-List | Out-Host
    $demoWanRows = foreach ($demoWan in @('wan1', 'wan2', 'wan3')) {
        $demoSample = $demoState.telemetry.$demoWan
        [pscustomobject]@{ WAN = $demoWan; Up = $demoSample.up; Fresh = $demoSample.fresh;
            RTT_ms = $demoSample.latency_ms; Jitter_ms = $demoSample.jitter_ms;
            Loss_pct = $demoSample.loss_pct; Utilization_pct = $demoSample.utilization_pct }
    }
    $demoWanRows | Format-Table -AutoSize | Out-Host
    Write-Host "Real traffic active: $($demoTraffic.active); starting: $($demoTraffic.starting)"
    Write-Host "Natural WAN active: $($demoNatural.active)"
    $demoState.last_decision | ConvertTo-Json -Depth 6
}

function Start-DemoWorkload {
    param([string]$Module, [int]$Duration)
    Stop-DemoWanLoads
    Invoke-LabPython -m lab.impairments reset | Out-Host
    Wait-DemoBaseline | Out-Null
    $demoLaunchText = @(Invoke-LabPython -m $Module start --background --duration $Duration) -join "`n"
    Write-Host $demoLaunchText
    if ($demoLaunchText -notmatch 'PID (\d+)') {
        Stop-DemoWanLoads
        throw 'Could not identify the new workload PID; inspect status/logs.'
    }
    $demoRunnerPid = [int]$Matches[1]
    try {
        for ($demoAttempt = 0; $demoAttempt -lt 45; $demoAttempt++) {
            $demoWorkload = Get-DemoWorkload -Module $Module
            if ($demoWorkload.pid -eq $demoRunnerPid) {
                if ($demoWorkload.active -and -not $demoWorkload.starting) {
                    Write-Host "Workload ready (PID $demoRunnerPid). Use .\demo.ps1 status or reset."
                    return
                }
                if (-not $demoWorkload.active -and -not $demoWorkload.starting) {
                    throw 'The new workload exited during startup. Inspect status/logs.'
                }
            }
            Start-Sleep -Seconds 2
        }
        throw 'Workload startup timed out; inspect status/logs.'
    } catch {
        # Stop a partially started runner while policy cleanup can still use the API.
        $demoStartupError = $_
        Stop-DemoWanLoads
        throw $demoStartupError
    }
}

function Invoke-DemoFault {
    param([string]$Scenario)
    Stop-DemoWanLoads
    Invoke-LabPython -m lab.impairments reset | Out-Host
    $demoState = Wait-DemoBaseline
    if (-not $Scenario) {
        $demoLink = $Target.ToLowerInvariant()
        if (-not $demoLink -or $demoLink -eq 'current') { $demoLink = $demoState.confirmed_path }
        $demoDownCases = @{ wan1 = 'S01'; wan2 = 'S02'; wan3 = 'S03' }
        if (-not $demoDownCases.ContainsKey($demoLink)) { throw 'No configured WAN available to cut.' }
        $Scenario = $demoDownCases[$demoLink]
        Write-Host "Down $demoLink ($Scenario); current configured WAN: $($demoState.confirmed_path)"
    }
    Write-Host "Keeping mode=$($demoState.mode), profile=$($demoState.profile)."
    if ($demoState.mode -eq 'manual') {
        Write-Host 'Approve/reject in Grafana or a second PowerShell terminal while this window waits.'
    }
    try {
        Invoke-LabPython -m lab.impairments apply --scenario $Scenario | Out-Host
        if ($Seconds -gt 0) {
            Write-Host "Fault active for $Seconds seconds; then restoring CONTROL."
            Start-Sleep -Seconds $Seconds
        } else {
            Read-Host 'Observe Grafana/ping; press Enter to restore all WANs' | Out-Null
        }
    } finally {
        Invoke-LabPython -m lab.impairments reset | Out-Host
        Write-Host 'CONTROL WAN baseline restored. Mode/profile were preserved.'
    }
}

function Reset-Demo {
    Stop-DemoWanLoads
    Invoke-LabPython -m lab.impairments reset | Out-Host
    if (Test-DemoApi) {
        Invoke-DemoApi -Path '/api/configuration' -Method Post -Body @{ mode = 'manual' } | Out-Null
        Wait-DemoIdle | Out-Null
        Write-Host 'All WANs restored; API left in Manual with its current profile.'
    } else {
        Write-Host 'All WANs restored; API is unavailable, so its mode could not be updated.'
    }
}

if ($Action -eq 'help') { Show-DemoHelp; return }

$demoOptionActions = @{
    Seconds = @('traffic', 'natural', 'down', 'fault')
    Mode = @('start')
    Profile = @('start', 'auto', 'manual', 'benchmark')
    NoML = @('start')
    NoOpen = @('start')
    Repetitions = @('benchmark')
    Json = @('status')
}
foreach ($demoOption in $demoOptionActions.Keys) {
    if ($PSBoundParameters.ContainsKey($demoOption) -and $Action -notin $demoOptionActions[$demoOption]) {
        throw "-$demoOption is not supported for '$Action'. Run .\demo.ps1 help."
    }
}

# Reject unsupported scenarios/targets before any network or policy mutation.
if ($Action -eq 'down' -and $Target -and $Target -notin @('current', 'wan1', 'wan2', 'wan3')) {
    throw 'Use down, down current, down wan1, down wan2 or down wan3.'
}
if ($Action -eq 'fault' -and $Target -notmatch '^S(0[1-9]|10)$') {
    throw 'Interactive fault supports S01..S10 only. Use the evaluator for telemetry/time-varying cases.'
}
if ($Action -eq 'profile' -and $Target -notin @('voice', 'interactive', 'bulk')) {
    throw 'Use profile voice, profile interactive or profile bulk.'
}
if ($Action -eq 'plan' -and $Target -notin @('traffic', 'natural') -and $Target -notmatch '^S(0[1-9]|10)$') {
    throw 'Use plan traffic, plan natural or plan S01..S10.'
}
if ($Action -eq 'traffic' -and $Seconds -gt 0 -and $Seconds -lt 15) {
    throw 'Real traffic duration must be 0 (continuous) or 15..7200 seconds.'
}
if ($Target -and $Action -notin @('down', 'fault', 'profile', 'plan')) {
    throw "Action '$Action' does not accept a target. Run .\demo.ps1 help."
}

Push-Location -LiteralPath $demoRoot
try {
    switch ($Action) {
        'start' {
            & (Join-Path $demoRoot 'scripts/start-wsl-lab.ps1') | Out-Host
            Stop-DemoWanLoads
            Stop-DemoApi
            $demoStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
            $demoStream = "live-demo-$demoStamp"
            Invoke-LabPython scripts/sync_edge_clocks.py --apply --force-sync `
                --output "reports/flexiwan/demo-$demoStamp-clock.json" | Out-Host
            Invoke-LabPython scripts/configure_lab_ospf.py --apply `
                --output "reports/flexiwan/demo-$demoStamp-ospf.json" | Out-Host
            Invoke-LabPython -m lab.impairments reset | Out-Host
            $demoLauncher = @('scripts/serve_live_lab.py', '--background', '--stream', $demoStream)
            if (-not $NoML) { $demoLauncher += '--experimental-synthetic-forecast' }
            Invoke-LabPython @demoLauncher | Out-Host
            $demoApiReady = $false
            for ($demoAttempt = 0; $demoAttempt -lt 30; $demoAttempt++) {
                if (Test-DemoApi) { $demoApiReady = $true; break }
                Start-Sleep -Seconds 2
            }
            if (-not $demoApiReady) { throw 'API startup failed/timed out. Inspect .\demo.ps1 logs.' }
            $demoState = Wait-DemoBaseline
            if (-not $NoML -and -not $demoState.model_loaded) {
                throw 'ML did not load. API remains in Manual; inspect logs or use start -NoML.'
            }
            Invoke-DemoApi -Path '/api/configuration' -Method Post `
                -Body @{ mode = $Mode; profile = $Profile } | Out-Null
            Write-Host "Ready: $demoStream; mode=$Mode; profile=$Profile; ML=$($demoState.model_loaded)."
            Write-Host 'Forecasts need about one minute of fresh history; model_loaded alone is not a forecast.'
            if (-not $NoOpen) { Open-DemoDashboard }
        }
        'open' {
            Open-DemoDashboard
            foreach ($demoUrl in @('https://localhost:8000/docs', 'https://localhost:18443/devices',
                'https://localhost:18443/tunnels', 'http://localhost:18081/', 'http://localhost:18082/')) {
                Start-Process $demoUrl
            }
        }
        'status' { Show-DemoStatus }
        'traffic' {
            $demoDuration = 210
            if ($demoSecondsSpecified) { $demoDuration = $Seconds }
            Start-DemoWorkload -Module 'lab.real_traffic' -Duration $demoDuration
            Write-Host 'Phases: 60s baseline / 90s WAN1 busy / 60s recovery. Read receiver + endpoint metrics.'
        }
        'natural' {
            $demoDuration = 180
            if ($demoSecondsSpecified) { $demoDuration = $Seconds }
            Start-DemoWorkload -Module 'lab.natural_wan' -Duration $demoDuration
        }
        'down' { Invoke-DemoFault }
        'fault' { Invoke-DemoFault -Scenario $Target.ToUpperInvariant() }
        'reset' { Reset-Demo }
        'stop' { Reset-Demo; Stop-DemoApi }
        { $_ -in @('auto', 'manual') } {
            Wait-DemoIdle | Out-Null
            $demoBody = @{ mode = $Action }
            if ($demoProfileSpecified) { $demoBody.profile = $Profile }
            Invoke-DemoApi -Path '/api/configuration' -Method Post -Body $demoBody |
                Select-Object mode, profile, confirmed_path
        }
        'profile' {
            Wait-DemoIdle | Out-Null
            Invoke-DemoApi -Path '/api/configuration' -Method Post -Body @{ profile = $Target } |
                Select-Object mode, profile, confirmed_path
        }
        { $_ -in @('approve', 'reject') } {
            $demoState = Get-DemoState
            if ($demoState.mode -ne 'manual' -or -not $demoState.pending) {
                throw 'No pending Manual recommendation. Inspect status/Grafana.'
            }
            $demoPending = $demoState.pending
            $demoResult = Invoke-DemoApi -Path "/api/recommendations/$($demoPending.id)" `
                -Method Post -Body @{ action = $Action }
            if ($Action -eq 'approve') {
                if ($demoResult.apply_inflight) { Write-Host 'Approved; waiting for agent readback...' }
                $demoResult = Wait-DemoIdle
                if ($demoResult.confirmed_path -ne $demoPending.target) {
                    throw 'Apply did not confirm the recommended WAN. Inspect decision audit/logs.'
                }
                Write-Host "Verified configured WAN: $($demoResult.confirmed_path)"
            } else { Write-Host 'Recommendation rejected.' }
        }
        'ping' {
            Invoke-DemoWsl -Arguments @('--exec', 'ip', 'netns', 'exec', 'sdw-client',
                'ping', '-O', '10.30.0.2')
        }
        'logs' {
            foreach ($demoLog in @('live-dashboard.log', 'real-traffic.log', 'natural-wan.log')) {
                Write-Host "--- $demoLog ---"
                # A workload log may not exist until its first run.
                & wsl.exe --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab `
                    --exec test -f "lab/runtime/logs/$demoLog"
                if ($LASTEXITCODE -eq 0) {
                    Invoke-DemoWsl -Arguments @('--exec', 'tail', '-n', '40', "lab/runtime/logs/$demoLog")
                } elseif ($LASTEXITCODE -ne 1) { throw 'Could not check the lab log file.' }
            }
            if (Test-DemoApi) {
                @(Invoke-DemoApi -Path '/api/events') | Select-Object -Last 15 | ConvertTo-Json -Depth 10
            }
        }
        'scenarios' { Invoke-LabPython -m sdwan scenarios --suite balanced }
        'plan' {
            switch ($Target) {
                'traffic' { Invoke-LabPython -m lab.real_traffic plan }
                'natural' { Invoke-LabPython -m lab.natural_wan plan }
                default { Invoke-LabPython -m lab.impairments plan --scenario $Target.ToUpperInvariant() }
            }
        }
        'benchmark' {
            Reset-Demo
            Stop-DemoApi
            $demoStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
            & (Join-Path $demoRoot 'scripts/run-wsl-experiment.ps1') `
                -OutputName "live-demo-s01-$demoStamp" -Scenarios S01 -Profiles $Profile `
                -Strategies native_priority,native_qbr,reactive,predictive_synthetic_lab `
                -Repetitions $Repetitions | Out-Host
            Write-Host 'Benchmark finished. Use .\demo.ps1 start to return to the live demo.'
        }
    }
} finally {
    Pop-Location
}
