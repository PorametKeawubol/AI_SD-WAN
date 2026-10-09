# Read current Windows readiness; save only project-local diagnostics.
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$computer = Get-CimInstance Win32_ComputerSystem
$operatingSystem = Get-CimInstance Win32_OperatingSystem
$features = @(Get-CimInstance Win32_OptionalFeature -Filter "Name='VirtualMachinePlatform' OR Name='Microsoft-Windows-Subsystem-Linux'" | Select-Object Name,InstallState)
$detailedFeatures = @()
if ($isAdministrator) {
    foreach ($featureName in @('VirtualMachinePlatform', 'Microsoft-Windows-Subsystem-Linux')) {
        $feature = Get-WindowsOptionalFeature -Online -FeatureName $featureName
        $detailedFeatures += [ordered]@{name=$featureName; state=$feature.State.ToString()}
    }
}
$cbsPending = Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
$snapshot = [ordered]@{
    timestamp_utc = [DateTime]::UtcNow.ToString('o')
    last_boot_utc = $operatingSystem.LastBootUpTime.ToUniversalTime().ToString('o')
    administrator = $isAdministrator
    hypervisor_present = [bool]$computer.HypervisorPresent
    features = $features
    detailed_features = $detailedFeatures
    servicing_reboot_pending = $cbsPending
    linux_hypervisor_ready = [bool]$computer.HypervisorPresent
    automatic_reboot_performed = $false
}
$reportPath = Join-Path $taskRoot 'reports\flexiwan\wsl2-readiness.json'
$json = $snapshot | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($reportPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
Write-Output $json
