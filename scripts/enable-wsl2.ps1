# Run explicitly from an Administrator PowerShell. No automatic reboot.
# Prepared after WSL reported HCS_E_SERVICE_NOT_AVAILABLE on 2026-10-01.
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open PowerShell as Administrator, then run this script again.'
}
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart
Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart
& bcdedit.exe /set hypervisorlaunchtype auto
if ($LASTEXITCODE -ne 0) { throw 'Could not configure the Windows hypervisor.' }
Write-Host 'Features enabled. Save your work and restart Windows, then open Docker Desktop.'
