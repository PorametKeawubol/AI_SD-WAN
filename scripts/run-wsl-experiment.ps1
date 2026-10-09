param(
    [Parameter(Mandatory = $true)][string]$OutputName,
    [string[]]$Scenarios = @('S01'),
    [string[]]$Profiles = @('voice'),
    [string[]]$Strategies = @('native_priority', 'native_qbr', 'reactive', 'predictive_synthetic_lab'),
    [ValidateRange(1,100)][int]$Repetitions = 3,
    [int]$OrderSeed = 91002,
    [ValidateSet('balanced','legacy')][string]$Suite = 'balanced'
)
$ErrorActionPreference = 'Stop'
if ($OutputName -notmatch '^[a-z0-9][a-z0-9_-]{2,100}$') { throw 'Use a fresh simple experiment output name.' }
if (-not ('AiSdwanLabPower' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AiSdwanLabPower {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern uint SetThreadExecutionState(uint flags);
}
'@
}
# Prevent idle system sleep only while this experiment command runs. Screen-off
# and explicit sleep/lid actions remain governed by the user's Windows settings.
$previousExecutionState = [AiSdwanLabPower]::SetThreadExecutionState([uint32]2147483649)
if ($previousExecutionState -eq 0) { throw 'Temporary system sleep request failed.' }
try {
    $labArguments = @('--distribution','AI-SDWAN-Lab','--user','root','--cd','/opt/ai-sdwan-lab','--exec','.venv/bin/python','scripts/run_live_lab.py','--output',$OutputName,'--suite',$Suite,'--scenarios')
    $labArguments += $Scenarios
    $labArguments += '--profiles'
    $labArguments += $Profiles
    $labArguments += '--strategies'
    $labArguments += $Strategies
    $labArguments += @('--repetitions', $Repetitions.ToString(), '--order-seed', $OrderSeed.ToString())
    & wsl.exe @labArguments
    if ($LASTEXITCODE -ne 0) { throw 'Experiment stopped; original trial artifacts were preserved.' }
} finally {
    [void][AiSdwanLabPower]::SetThreadExecutionState([uint32]2147483648)
}
