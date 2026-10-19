param([ValidateSet('Diagnose','Rollback','Verify')][string]$Mode='Diagnose')
# Windows servicing is opt-in. This script never reboots or disables security settings.
$ErrorActionPreference='Stop'
$taskRoot=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$reportDirectory=Join-Path $taskRoot 'reports\flexiwan\kvm-repair-20261017'
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
if($Mode -eq 'Rollback'){
    Start-Transcript -Path (Join-Path $reportDirectory 'rollback-transcript.txt') -Append | Out-Null
}
$taskIdentity=[Security.Principal.WindowsIdentity]::GetCurrent()
$taskPrincipal=[Security.Principal.WindowsPrincipal]::new($taskIdentity)
$taskAdmin=$taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$taskSystem=Get-CimInstance Win32_OperatingSystem
$taskComputer=Get-CimInstance Win32_ComputerSystem
$taskCpu=Get-CimInstance Win32_Processor | Select-Object -First 1
$packageName='Package_for_RollupFix~31bf3856ad364e35~amd64~~26100.9711.1.30'
$manifest=Join-Path $env:WINDIR ('servicing\Packages\'+$packageName+'.mum')
$taskKB=Get-HotFix -Id KB5127648 -ErrorAction SilentlyContinue
$linuxCheck=@'
import fcntl,json,os,subprocess
r={'svm': 'svm' in open('/proc/cpuinfo').read().split(), 'device_exists':os.path.exists('/dev/kvm')}
p=subprocess.run(['modprobe','kvm_amd'],capture_output=True,text=True)
r['module_exit']=p.returncode
r['module_error']=p.stderr.strip()
r['kvm_api_version']=None
r['create_vm_verified']=False
try:
    fd=os.open('/dev/kvm',os.O_RDWR)
    try:
        r['kvm_api_version']=fcntl.ioctl(fd,0xAE00,0)
        vm=fcntl.ioctl(fd,0xAE01,0)
        os.close(vm)
        r['create_vm_verified']=True
    finally:
        os.close(fd)
except OSError as e:
    r['device_error']=str(e)
print(json.dumps(r))
'@
$taskLinuxRaw=& wsl.exe -d AI-SDWAN-Lab -u root --exec python3 -c $linuxCheck
if($LASTEXITCODE -ne 0){throw 'Could not diagnose the existing AI-SDWAN-Lab distro.'}
$taskLinux=$taskLinuxRaw | ConvertFrom-Json
$taskReport=[ordered]@{
    timestamp_utc=[DateTime]::UtcNow.ToString('o'); mode=$Mode; administrator=$taskAdmin
    windows_version=$taskSystem.Version; windows_build=$taskSystem.BuildNumber
    cpu=$taskCpu.Name; hypervisor_present=$taskComputer.HypervisorPresent
    firmware_virtualization=$taskCpu.VirtualizationFirmwareEnabled
    kb5127648_installed=[bool]$taskKB; exact_manifest_present=(Test-Path -LiteralPath $manifest)
    package_name=$packageName; linux=$taskLinux; rollback_performed=$false
    automatic_reboot_performed=$false
    upstream_issue='https://github.com/microsoft/WSL/issues/41917'
}
$taskReportPath=Join-Path $reportDirectory ($Mode.ToLowerInvariant()+'.json')
$taskReport | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $taskReportPath -Encoding UTF8
if($Mode -eq 'Rollback'){
    if(-not $taskAdmin){throw 'Rollback requires an Administrator PowerShell. No Windows changes were made.'}
    if($taskLinux.create_vm_verified){throw 'KVM already works; rollback refused.'}
    if(-not $taskKB -or -not (Test-Path -LiteralPath $manifest)){throw 'The exact affected KB/package is absent; rollback refused.'}
    [xml]$taskManifest=Get-Content -LiteralPath $manifest -Raw
    if($taskManifest.assembly.package.identifier -ne 'KB5127648'){throw 'Package identity mismatch.'}
    if($taskSystem.BuildNumber -notin @('26100','26200','26300')){throw 'Unexpected Windows build; rollback refused.'}
    if(Test-Path -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'){
        throw 'Windows already has a pending servicing reboot. Finish it before rollback.'
    }
    $taskPackage=Get-WindowsPackage -Online -PackageName $packageName
    if($taskPackage.PackageState -ne 'Installed'){throw 'The exact LCU is not in Installed state.'}
    Copy-Item -LiteralPath $manifest -Destination (Join-Path $reportDirectory 'affected-package.mum') -Force
    $taskPackage | Select-Object PackageName,PackageState,ReleaseType,InstallTime | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reportDirectory 'package-before.json') -Encoding UTF8
    Write-Output 'Removing only KB5127648 LCU. SSU remains installed. Automatic restart is disabled.'
    $taskRemoval=Remove-WindowsPackage -Online -PackageName $packageName -NoRestart -LogPath (Join-Path $reportDirectory 'dism-rollback.log')
    $taskReport.rollback_performed=$true
    $taskReport['restart_needed']=[bool]$taskRemoval.RestartNeeded
    $taskReport | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $taskReportPath -Encoding UTF8
    Write-Output 'Rollback was requested successfully. Save your work and restart Windows yourself, then run -Mode Verify.'
} elseif($Mode -eq 'Verify'){
    if(-not $taskLinux.create_vm_verified -or $taskLinux.kvm_api_version -ne 12){throw 'KVM_CREATE_VM did not succeed. Do not start the packet experiment.'}
    Write-Output 'KVM API 12 and KVM_CREATE_VM both verified. The existing lab can now be resumed.'
}
$taskReport | ConvertTo-Json -Depth 6
