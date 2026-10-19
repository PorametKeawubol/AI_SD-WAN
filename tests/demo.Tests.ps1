# Run with Windows PowerShell: Invoke-Pester .\tests\demo.Tests.ps1
# All native, HTTP, token, interactive, and browser calls are mocked.
$demoTestRepo = Split-Path -Parent $PSScriptRoot
$demoTestScript = Join-Path $demoTestRepo 'demo.ps1'

Describe 'Demo command safety and behavior' {
    BeforeEach {
        $global:AiSdwanDemoTest = @{
            NativeCalls = New-Object System.Collections.ArrayList
            ApiCalls = New-Object System.Collections.ArrayList
            Browsers = New-Object System.Collections.ArrayList
            ReadFailure = $false
            ApplyFailure = $false
            CleanupFailure = $false
            ApprovalIssued = $false
            ApprovalPolls = 0
            ApiPidFileExists = $true
            WorkloadStarted = $false
            WorkloadModule = ''
            WorkloadPolls = 0
            WorkloadReadFailure = $false
            CallOrder = New-Object System.Collections.ArrayList
            Real = @{ active = $false; starting = $false; restored_baseline = $true; cleanup_errors = @() }
            Natural = @{ active = $false; restored_baseline = $true }
            State = [pscustomobject]@{
                mode = 'manual'
                profile = 'bulk'
                backend = 'flexiwan'
                confirmed_path = 'wan2'
                network_available = $true
                model_loaded = $true
                model_error = $null
                telemetry = [pscustomobject]@{
                    wan1 = [pscustomobject]@{ up = $true; fresh = $true; age_seconds = 0 }
                    wan2 = [pscustomobject]@{ up = $true; fresh = $true; age_seconds = 0 }
                    wan3 = [pscustomobject]@{ up = $true; fresh = $true; age_seconds = 0 }
                }
                pending = $null
                apply_inflight = $null
                last_decision = $null
            }
        }

        Mock wsl.exe {
            $nativeLine = @($args) -join ' '
            [void]$global:AiSdwanDemoTest.NativeCalls.Add($nativeLine)
            [void]$global:AiSdwanDemoTest.CallOrder.Add($nativeLine)
            $global:LASTEXITCODE = 0
            if ($nativeLine -match 'lab\.(real_traffic|natural_wan) start --background --duration (\d+)') {
                $global:AiSdwanDemoTest.WorkloadStarted = $true
                $global:AiSdwanDemoTest.WorkloadModule = 'lab.' + $Matches[1]
                return 'Workload starting: PID 456; stream live-demo-test'
            } elseif ($nativeLine -match 'lab\.(real_traffic|natural_wan) stop') {
                $global:AiSdwanDemoTest.WorkloadStarted = $false
            } elseif ($nativeLine -match 'lab\.(real_traffic|natural_wan) status' -and
                $global:AiSdwanDemoTest.WorkloadStarted -and
                ('lab.' + $Matches[1]) -eq $global:AiSdwanDemoTest.WorkloadModule) {
                $global:AiSdwanDemoTest.WorkloadPolls++
                if ($global:AiSdwanDemoTest.WorkloadReadFailure) { throw 'Test workload state read failed' }
                if ($global:AiSdwanDemoTest.WorkloadPolls -eq 1) {
                    return (@{ pid = 123; active = $true; starting = $false } | ConvertTo-Json -Compress)
                }
                return (@{ pid = 456; active = $true; starting = $false } | ConvertTo-Json -Compress)
            } elseif ($nativeLine -match 'lab\.real_traffic status') {
                if ($global:AiSdwanDemoTest.CleanupFailure) {
                    @{ active = $false; starting = $false; restored_baseline = $false; cleanup_errors = @('Network cleanup failed') } | ConvertTo-Json -Compress
                } else {
                    $global:AiSdwanDemoTest.Real | ConvertTo-Json -Compress
                }
            } elseif ($nativeLine -match 'lab\.natural_wan status') {
                $global:AiSdwanDemoTest.Natural | ConvertTo-Json -Compress
            } elseif ($nativeLine -match 'test -f .*live-dashboard\.pid') {
                if (-not $global:AiSdwanDemoTest.ApiPidFileExists) { $global:LASTEXITCODE = 1 }
            } elseif ($nativeLine -match 'lab\.impairments apply' -and $global:AiSdwanDemoTest.ApplyFailure) {
                $global:LASTEXITCODE = 23
            }
        }
        Mock Get-Content {
            $requestedFile = if ($LiteralPath) { [string]$LiteralPath } else { [string]$Path }
            if ($requestedFile -match 'api-token\.txt$') { return 'test-token-not-a-secret' }
            throw "Unexpected file read in demo test: $requestedFile"
        }
        Mock Test-Path { $true } -ParameterFilter { $LiteralPath -match 'api-token\.txt$' }
        Mock Invoke-RestMethod {
            $requestUri = [string]$Uri
            $requestMethod = if ($Method) { [string]$Method } else { 'Get' }
            [void]$global:AiSdwanDemoTest.ApiCalls.Add(@{
                Uri = $requestUri; Method = $requestMethod; Body = $Body
            })
            [void]$global:AiSdwanDemoTest.CallOrder.Add("$requestMethod $requestUri")
            if ($requestUri -match '/health$') {
                return @{ status = 'ok'; backend = 'flexiwan'; model_loaded = $true }
            }
            if ($requestUri -match '/api/state$') {
                if ($global:AiSdwanDemoTest.ApprovalIssued) {
                    $global:AiSdwanDemoTest.ApprovalPolls++
                    if ($global:AiSdwanDemoTest.ApprovalPolls -ge 2) {
                        $global:AiSdwanDemoTest.State.apply_inflight = $null
                        $global:AiSdwanDemoTest.State.confirmed_path = 'wan3'
                    }
                }
                return $global:AiSdwanDemoTest.State
            }
            if ($requestUri -match '/api/configuration$') {
                $configuration = $Body | ConvertFrom-Json
                if ($configuration.PSObject.Properties.Name -contains 'mode') {
                    $global:AiSdwanDemoTest.State.mode = $configuration.mode
                }
                if ($configuration.PSObject.Properties.Name -contains 'profile') {
                    $global:AiSdwanDemoTest.State.profile = $configuration.profile
                }
                return $global:AiSdwanDemoTest.State
            }
            if ($requestUri -match '/api/recommendations/') {
                $review = $Body | ConvertFrom-Json
                if ($review.action -eq 'approve') {
                    $global:AiSdwanDemoTest.ApprovalIssued = $true
                    $global:AiSdwanDemoTest.State.pending = $null
                    $global:AiSdwanDemoTest.State.apply_inflight = @{ id = 'fresh-pending'; target = 'wan3' }
                }
                return $global:AiSdwanDemoTest.State
            }
            if ($requestUri -match '/api/events$') { return @() }
            throw "Unexpected HTTP call in demo test: $requestUri"
        }
        Mock Start-Process {
            [void]$global:AiSdwanDemoTest.Browsers.Add([string]$FilePath)
        }
        Mock Read-Host {
            if ($global:AiSdwanDemoTest.ReadFailure) { throw 'Demo test interrupted the prompt' }
            return ''
        }
        Mock Start-Sleep {}
    }

    AfterEach {
        Remove-Variable -Name AiSdwanDemoTest -Scope Global -ErrorAction SilentlyContinue
    }

    It 'shows help without accessing WSL, HTTP, token, or browser' {
        & $demoTestScript help | Out-Null
        Assert-MockCalled wsl.exe -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-RestMethod -Times 0 -Exactly -Scope It
        Assert-MockCalled Get-Content -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It
    }

    It 'keeps Manual mode and restores the WAN when the prompt is interrupted' {
        $global:AiSdwanDemoTest.ReadFailure = $true
        { & $demoTestScript down wan1 | Out-Null } | Should Throw
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object { $_ -match 'lab\.impairments apply .*S01' }).Count | Should Be 1
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object { $_ -match 'lab\.impairments reset' }).Count | Should Be 2
        $global:AiSdwanDemoTest.NativeCalls[-1] | Should Match 'lab\.impairments reset'
        @($global:AiSdwanDemoTest.ApiCalls | Where-Object { $_.Uri -match '/api/configuration$' }).Count | Should Be 0
        $global:AiSdwanDemoTest.State.mode | Should Be 'manual'
    }

    It 'restores the WAN after a native apply error and surfaces the error' {
        $global:AiSdwanDemoTest.ApplyFailure = $true
        { & $demoTestScript down wan1 | Out-Null } | Should Throw
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object { $_ -match 'lab\.impairments reset' }).Count | Should Be 2
        $global:AiSdwanDemoTest.NativeCalls[-1] | Should Match 'lab\.impairments reset'
        Assert-MockCalled Read-Host -Times 0 -Exactly -Scope It
    }

    It 'blocks fault apply if workload restoration failed' {
        $global:AiSdwanDemoTest.CleanupFailure = $true
        { & $demoTestScript down wan1 | Out-Null } | Should Throw
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object { $_ -match 'lab\.impairments apply' }).Count | Should Be 0
    }

    It 'blocks API stop if workload restoration failed' {
        $global:AiSdwanDemoTest.CleanupFailure = $true
        { & $demoTestScript stop | Out-Null } | Should Throw
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object { $_ -match 'serve_live_lab\.py --stop' }).Count | Should Be 0
    }

    It 'changes only mode when profile is omitted' {
        & $demoTestScript auto | Out-Null
        $autoPost = @($global:AiSdwanDemoTest.ApiCalls | Where-Object { $_.Uri -match '/api/configuration$' })[-1]
        $autoBody = $autoPost.Body | ConvertFrom-Json
        $autoBody.mode | Should Be 'auto'
        ($autoBody.PSObject.Properties.Name -contains 'profile') | Should Be $false
        $global:AiSdwanDemoTest.State.profile | Should Be 'bulk'

        & $demoTestScript manual | Out-Null
        $manualPost = @($global:AiSdwanDemoTest.ApiCalls | Where-Object { $_.Uri -match '/api/configuration$' })[-1]
        ($manualPost.Body | ConvertFrom-Json).mode | Should Be 'manual'
        $global:AiSdwanDemoTest.State.profile | Should Be 'bulk'
    }

    It 'sets a profile when explicitly requested' {
        & $demoTestScript manual -Profile voice | Out-Null
        $configurationPost = @($global:AiSdwanDemoTest.ApiCalls | Where-Object { $_.Uri -match '/api/configuration$' })[-1]
        ($configurationPost.Body | ConvertFrom-Json).profile | Should Be 'voice'
    }

    It 'resolves current to the confirmed WAN' {
        & $demoTestScript down current | Out-Null
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object { $_ -match 'lab\.impairments apply .*S02' }).Count | Should Be 1
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object { $_ -match 'lab\.impairments reset' }).Count | Should Be 2
    }

    It 'approves the freshly fetched pending ID and waits for verified target' {
        $global:AiSdwanDemoTest.State.pending = @{ id = 'fresh-pending'; source = 'wan2'; target = 'wan3' }
        & $demoTestScript approve | Out-Null
        $reviewPost = @($global:AiSdwanDemoTest.ApiCalls | Where-Object { $_.Uri -match '/api/recommendations/' })
        $reviewPost.Count | Should Be 1
        $reviewPost[0].Uri | Should Match '/api/recommendations/fresh-pending$'
        ($reviewPost[0].Body | ConvertFrom-Json).action | Should Be 'approve'
        ($global:AiSdwanDemoTest.ApprovalPolls -ge 2) | Should Be $true
        $global:AiSdwanDemoTest.State.confirmed_path | Should Be 'wan3'
    }

    It 'does not POST an approval without a pending recommendation' {
        try { & $demoTestScript approve | Out-Null } catch { }
        @($global:AiSdwanDemoTest.ApiCalls | Where-Object { $_.Uri -match '/api/state$' }).Count | Should Be 1
        @($global:AiSdwanDemoTest.ApiCalls | Where-Object { $_.Uri -match '/api/recommendations/' }).Count | Should Be 0
    }

    It 'rejects unsupported faults before any mutation' {
        { & $demoTestScript fault S11 | Out-Null } | Should Throw
        Assert-MockCalled wsl.exe -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-RestMethod -Times 0 -Exactly -Scope It
    }

    It 'restores the caller location after a failed interactive demo' {
        $originalLocation = (Get-Location).Path
        Push-Location $TestDrive
        try {
            $beforeDemo = (Get-Location).Path
            $global:AiSdwanDemoTest.ReadFailure = $true
            { & $demoTestScript down wan3 | Out-Null } | Should Throw
            (Get-Location).Path | Should Be $beforeDemo
        } finally {
            Pop-Location
        }
        (Get-Location).Path | Should Be $originalLocation
    }

    It 'waits for the new workload PID instead of accepting stale active state' {
        & $demoTestScript traffic | Out-Null
        $global:AiSdwanDemoTest.WorkloadPolls | Should Be 2
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object {
            $_ -match 'lab\.real_traffic start --background --duration 210'
        }).Count | Should Be 1
    }

    It 'supports continuous traffic and finite natural profiles' {
        & $demoTestScript traffic -Seconds 0 | Out-Null
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object {
            $_ -match 'lab\.real_traffic start --background --duration 0$'
        }).Count | Should Be 1
        & $demoTestScript natural | Out-Null
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object {
            $_ -match 'lab\.natural_wan start --background --duration 180$'
        }).Count | Should Be 1
    }

    It 'stops a new workload if its readiness status cannot be read' {
        $global:AiSdwanDemoTest.WorkloadReadFailure = $true
        { & $demoTestScript traffic | Out-Null } | Should Throw
        $global:AiSdwanDemoTest.WorkloadStarted | Should Be $false
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object { $_ -match 'lab\.real_traffic stop' }).Count | Should Be 2
    }

    It 'does not call API stop when its saved PID file is absent' {
        $global:AiSdwanDemoTest.ApiPidFileExists = $false
        & $demoTestScript stop | Out-Null
        @($global:AiSdwanDemoTest.NativeCalls | Where-Object { $_ -match 'serve_live_lab\.py --stop' }).Count | Should Be 0
    }

    It 'normalizes case before sending API configuration' {
        & $demoTestScript AUTO -Profile BULK | Out-Null
        $caseBody = (@($global:AiSdwanDemoTest.ApiCalls | Where-Object { $_.Uri -match '/api/configuration$' })[-1].Body) | ConvertFrom-Json
        $caseBody.mode | Should Be 'auto'
        $caseBody.profile | Should Be 'bulk'
    }

    It 'rejects unsupported options without modifying the lab' {
        { & $demoTestScript traffic -Mode manual | Out-Null } | Should Throw
        Assert-MockCalled wsl.exe -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-RestMethod -Times 0 -Exactly -Scope It
    }

    It 'starts with ML only after API restart, clock sync and WAN readiness' {
        $startFixture = Join-Path $TestDrive 'start-fixture'
        New-Item -ItemType Directory -Path (Join-Path $startFixture 'scripts') -Force | Out-Null
        Copy-Item -LiteralPath $demoTestScript -Destination (Join-Path $startFixture 'demo.ps1')
        [System.IO.File]::WriteAllText((Join-Path $startFixture 'scripts/start-wsl-lab.ps1'), '# fixture: no lab resume')
        & (Join-Path $startFixture 'demo.ps1') start -NoOpen | Out-Null
        $callOrder = @($global:AiSdwanDemoTest.CallOrder)
        $apiStop = @($callOrder | Where-Object { $_ -match 'serve_live_lab\.py --stop' })[0]
        $clockSync = @($callOrder | Where-Object { $_ -match 'sync_edge_clocks\.py' })[0]
        $ospf = @($callOrder | Where-Object { $_ -match 'configure_lab_ospf\.py' })[0]
        $apiStart = @($callOrder | Where-Object { $_ -match 'serve_live_lab\.py --background' })[0]
        ([Array]::IndexOf($callOrder, $apiStop) -lt [Array]::IndexOf($callOrder, $clockSync)) | Should Be $true
        ([Array]::IndexOf($callOrder, $clockSync) -lt [Array]::IndexOf($callOrder, $ospf)) | Should Be $true
        ([Array]::IndexOf($callOrder, $ospf) -lt [Array]::IndexOf($callOrder, $apiStart)) | Should Be $true
        $apiStart | Should Match '--experimental-synthetic-forecast'
        $lastConfig = (@($global:AiSdwanDemoTest.ApiCalls | Where-Object { $_.Uri -match '/api/configuration$' })[-1].Body) | ConvertFrom-Json
        $lastConfig.mode | Should Be 'auto'
        $lastConfig.profile | Should Be 'voice'
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It
    }

    It 'leaves startup in Manual if the requested ML model is unavailable' {
        $startFixture = Join-Path $TestDrive 'failed-model-fixture'
        New-Item -ItemType Directory -Path (Join-Path $startFixture 'scripts') -Force | Out-Null
        Copy-Item -LiteralPath $demoTestScript -Destination (Join-Path $startFixture 'demo.ps1')
        [System.IO.File]::WriteAllText((Join-Path $startFixture 'scripts/start-wsl-lab.ps1'), '# fixture: no lab resume')
        $global:AiSdwanDemoTest.State.model_loaded = $false
        { & (Join-Path $startFixture 'demo.ps1') start -NoOpen | Out-Null } | Should Throw
        $global:AiSdwanDemoTest.State.mode | Should Be 'manual'
        @($global:AiSdwanDemoTest.ApiCalls | Where-Object {
            $_.Uri -match '/api/configuration$' -and ($_.Body | ConvertFrom-Json).mode -eq 'auto'
        }).Count | Should Be 0
    }
}
