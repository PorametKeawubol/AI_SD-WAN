# KVM recovery on the affected Windows host

Status at 21:03 Bangkok time: the user explicitly approved removal of KB5127648 and chose to restart Windows themselves. The elevated rollback finished successfully with `rollback_performed=true`, `restart_needed=true` and `automatic_reboot_performed=false` in `reports/flexiwan/kvm-repair-20261017/rollback.json`. Windows has not been restarted. KVM and flexiEdge still require post-reboot verification before any new live experiments.

Subsequent integration checks temporarily booted the two existing flexiEdge guests with QEMU TCG software emulation, 4 vCPU and 4 GB each. This did not recover KVM. Six S12 voice trials completed with real UDP and PCAP evidence; results and limitations are in `reports/flexiedge-candidate-validation-20261018/validation.json`. After testing, the original policy was read back on both agents, the temporary sampler and SSH masters were stopped, both guests were shut down gracefully, and their exact original KVM/2-vCPU definitions were restored and reloaded by libvirt. Controller, TimescaleDB and Grafana remain running. `/dev/kvm` still returns ENODEV; no further update removal or automatic reboot was performed.

The 17 October checks found Windows 11 build 26300.9711 with KB5127648 installed, firmware virtualization enabled, and Hyper-V active. WSL has no `svm` flag. Loading `kvm_amd` fails with `Operation not supported`; opening the existing `/dev/kvm` node fails with `ENODEV`. Neither KVM API 12 nor KVM_CREATE_VM succeeds.

Explicit `nestedVirtualization=true` and a complete WSL shutdown/restart were tested. They did not restore KVM. The existing two flexiEdge guests remain shut off. Their disks and VM definitions were preserved; controller, database, and Grafana services are running again.

Microsoft confirms the affected Windows update in [WSL issue 41917](https://github.com/microsoft/WSL/issues/41917). [PR 41942](https://github.com/microsoft/WSL/pull/41942) adds a warning and explicitly does not fix the underlying Windows regression. Reports in the issue describe recovery after removing KB5127648 and restarting Windows. This is a reported workaround, not a successful repair on this host yet.

`scripts/repair-wsl-kvm.ps1` defaults to read-only diagnosis. Rollback is an explicit separate mode, requires Administrator PowerShell, verifies the exact installed package and KB manifest, and refuses an existing pending servicing restart. It removes only the LCU, keeps the servicing stack, never changes security settings, and never restarts Windows automatically. Removing this security LCU temporarily removes its fixes; user approval is required before invoking rollback.

```powershell
# No Windows changes; save a fresh diagnostic report.
.\scripts\repair-wsl-kvm.ps1 -Mode Diagnose

# After explicit approval, from Administrator PowerShell:
.\scripts\repair-wsl-kvm.ps1 -Mode Rollback

# Save work and restart Windows, then:
.\scripts\repair-wsl-kvm.ps1 -Mode Verify
.\scripts\start-wsl-lab.ps1
```

After Verify succeeds, synchronize edge clocks and OSPF timers, then run preflight before collecting new flexiEdge traffic. Do not label the veth model as a validated flexiEdge model or replace the live model merely because KVM can start.

The exact package found locally is `Package_for_RollupFix~31bf3856ad364e35~amd64~~26100.9711.1.30`. The manifest identifies KB5127648 as a Security Update. Microsoft documents [DISM removal of the LCU from combined SSU/LCU packages](https://support.microsoft.com/en-gb/servicing/os/windows-11/2026/03/march-31-2026-kb5086672-os-builds-26200-8117-and-26100-8117-out-of-band). The script uses the corresponding `Remove-WindowsPackage -NoRestart` API. It does not use WUSA to remove the combined package.
