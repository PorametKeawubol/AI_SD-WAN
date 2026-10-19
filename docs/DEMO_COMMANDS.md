# คำสั่งสาธิต AI SD-WAN บนเครื่องนี้

## เรียกผ่านสคริปต์แบบสั้น

ใช้ [`demo.ps1`](../demo.ps1) ได้เลย ไม่ต้องคัดลอกฟังก์ชันหรืออ่าน token เอง:

```powershell
Set-Location E:\Proposal\AI_SD-WAN
.\demo.ps1 start
.\demo.ps1 traffic
```

`start` เปิดแล็บ ตรวจเวลาและ OSPF เปิด API พร้อม ML ทดลอง รอข้อมูล WAN สด แล้วเปิด Auto/Voice และ Grafana ส่วน `traffic` รันโหลดจริง 210 วินาที หากเรียกคำสั่งทดสอบรอบถัดไปก่อนจบรอบ สคริปต์จะหยุด workload เดิมและตรวจการคืนค่าก่อนเสมอ

รอให้ขึ้น `Ready: ... mode=auto; profile=voice; ML=True` ก่อนเริ่มเดโม ขั้นตอนหยุด API เดิมรอได้ 60 วินาที และการซิงก์เวลา VM ลองซ้ำได้ไม่เกิน 3 ครั้งเมื่อ SSH ช้า โดยยังต้องผ่านเกณฑ์เวลาเดิมก่อนเปิด API

หลังดูโหลดจริงแล้ว ทดสอบ Down โดยใช้เส้นที่มี configured priority อยู่ในขณะนั้น:

```powershell
.\demo.ps1 down
```

คำสั่งรอให้ดู Grafana แล้วกด Enter เพื่อคืน WAN ทุกเส้น หากต้องการระบุเส้นหรือเวลา ใช้ `.\demo.ps1 down wan1` หรือ `.\demo.ps1 down wan1 -Seconds 30` คำสั่ง Down/Fault คงโหมด Auto/Manual เดิมไว้ หากเพิ่งรัน `reset` ต้องเรียก `auto` ก่อนเมื่อจะสาธิตการปรับ policy อัตโนมัติ

| คำสั่ง | การใช้งาน |
|---|---|
| `.\demo.ps1 help` | แสดงคำสั่งทั้งหมด; เรียกโดยไม่ใส่ action ก็ได้ |
| `.\demo.ps1 start` | เปิดระบบพร้อม ML ทดลอง, Auto/Voice และ Grafana |
| `.\demo.ps1 start -Mode manual` | เปิดระบบใน Manual สำหรับ Approve/Reject |
| `.\demo.ps1 start -NoML -NoOpen` | เปิด API แบบ reactive และไม่เปิด browser |
| `.\demo.ps1 open` | เปิด Grafana, Swagger, Community Devices/Tunnels และ GUI ของ Edge ทั้งสอง |
| `.\demo.ps1 status` | อ่านสถานะ WAN, ML, mode/profile, pending และ workloads |
| `.\demo.ps1 status -Json` | อ่านข้อมูลสถานะเต็มแบบ JSON |
| `.\demo.ps1 traffic` | ทราฟฟิกจริงและโหลดพื้นหลัง 210 วินาที |
| `.\demo.ps1 traffic -Seconds 0` | วนโหลดจริงจนกว่าจะเรียก reset/stop |
| `.\demo.ps1 natural` | โปรไฟล์ไฟเบอร์/บรอดแบนด์/มือถือ 180 วินาที |
| `.\demo.ps1 natural -Seconds 0` | โปรไฟล์ WAN ต่อเนื่องจนกว่าจะเรียก reset/stop |
| `.\demo.ps1 down` | ตัดเส้นที่มี configured priority ปัจจุบัน แล้วรอ Enter เพื่อคืนค่า |
| `.\demo.ps1 down wan2 -Seconds 30` | ตัด WAN2 เป็นเวลา 30 วินาที แล้วคืนค่า |
| `.\demo.ps1 fault S08` | WAN1 latency สูง; ใช้ S01–S10 ได้ แล้วกด Enter เพื่อคืนค่า |
| `.\demo.ps1 ping` | ping ข้ามไซต์จริง; รันอีกหน้าต่างและหยุดด้วย Ctrl+C |
| `.\demo.ps1 manual` | เปลี่ยนเป็น Manual โดยคง profile เดิม |
| `.\demo.ps1 approve` | อ่าน ID ของ pending ล่าสุดแล้วอนุมัติ รอ verified configured priority |
| `.\demo.ps1 reject` | ปฏิเสธ pending ล่าสุด |
| `.\demo.ps1 auto` | เปลี่ยนเป็น Auto โดยคง profile เดิม |
| `.\demo.ps1 profile bulk` | เปลี่ยน profile; เลือก voice / interactive / bulk |
| `.\demo.ps1 reset` | หยุดโหลด ตรวจ cleanup คืน WAN ทุกเส้น และคง API ไว้ใน Manual |
| `.\demo.ps1 stop` | ทำ reset แล้วหยุด API; VMs/controller/Grafana ยังทำงาน |
| `.\demo.ps1 logs` | อ่าน log ล่าสุดและ decision events |
| `.\demo.ps1 scenarios` | ดู catalogue balanced ทั้ง 38 cases |
| `.\demo.ps1 plan traffic` | ดูแผนโดยไม่ฉีด fault; ใช้ natural หรือ S01–S10 ได้ด้วย |
| `.\demo.ps1 benchmark` | รัน S01 สี่วิธี วิธีละสามรอบ; หยุด live API ก่อนรัน |

### เดโมแต่ละ scenario ด้วยคำสั่งสั้น

เปิด Auto ด้วย `.\demo.ps1 auto` แล้วเลือกคำสั่งในตารางทีละเหตุการณ์ ดูผลใน Grafana และกด Enter ในหน้าต่างคำสั่งเพื่อคืน WAN ทุกเส้น โหมด Auto/Manual เดิมยังคงอยู่หลังจบแต่ละ fault

| Scenario | เหตุการณ์ | คำสั่ง |
|---|---|---|
| S01 | WAN1 Down | `.\demo.ps1 fault S01` |
| S02 | WAN2 Down | `.\demo.ps1 fault S02` |
| S03 | WAN3 Down | `.\demo.ps1 fault S03` |
| S04 | WAN1 และ WAN2 Down | `.\demo.ps1 fault S04` |
| S05 | WAN1 และ WAN3 Down | `.\demo.ps1 fault S05` |
| S06 | WAN2 และ WAN3 Down | `.\demo.ps1 fault S06` |
| S07 | ทุก WAN Down; ไม่มีเส้นทางที่ใช้ได้ | `.\demo.ps1 fault S07` |
| S08 | WAN1 latency สูง | `.\demo.ps1 fault S08` |
| S09 | WAN1 jitter สูง | `.\demo.ps1 fault S09` |
| S10 | WAN1 packet loss สูง | `.\demo.ps1 fault S10` |

ถ้าต้องการคืนค่าอัตโนมัติ เช่น เดโม latency 90 วินาที ใช้ `.\demo.ps1 fault S08 -Seconds 90` ก่อนเริ่มดู `.\demo.ps1 status` เพื่อทราบเส้นที่มี configured priority อยู่ หากตัดเส้นสำรองอาจไม่มีการเปลี่ยน priority; `.\demo.ps1 down` เลือกตัดเส้นปัจจุบันให้เอง

เปิด PowerShell อีกหน้าต่างแล้วเรียก `.\demo.ps1 ping` เพื่อแสดงการรับส่งข้ามไซต์จริง ดูร่วมกับ WAN health, Configured priority และ Decision and approval audit ใน Grafana เมื่อคืนลิงก์แล้วรอให้ข้อมูลสดและงาน apply จบก่อนเริ่มเหตุการณ์ถัดไป สคริปต์ตรวจเงื่อนไขนี้ให้

ใช้ `.\demo.ps1 plan S08` เพื่อดูค่าที่จะฉีดโดยไม่เปลี่ยน WAN และ `.\demo.ps1 scenarios` เพื่อดู catalogue ทั้งหมด 38 cases คำสั่ง `fault` สำหรับเดโมรองรับ S01–S10; cases ที่เปลี่ยนตามเวลาหรือทดสอบ telemetry ใช้ live evaluator ตามรายละเอียดด้านล่าง

Manual demo ใช้สองหน้าต่าง: หน้าต่างแรกเรียก `manual` แล้ว `down`; ระหว่างที่รอ Enter ให้หน้าต่างที่สองเรียก `approve` หรือ `reject` หรือใช้ปุ่มใน Grafana คำแนะนำมีอายุจำกัด จึงต้องอ่าน pending ใหม่ก่อนอนุมัติ สคริปต์ทำขั้นตอนนี้ให้แล้ว

สคริปต์อ้างตำแหน่งไฟล์จากโฟลเดอร์ของตัวเองและคืน working directory เดิมเมื่อจบ สำหรับเครื่องที่บล็อกไฟล์ PowerShell ให้เปิดเฉพาะ process ด้วย `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\demo.ps1 start` โดยไม่แก้ execution policy ของเครื่อง

รายละเอียดคำสั่งภายในและขอบเขตการทดสอบอยู่ด้านล่าง

---

ใช้ Windows PowerShell ที่ `E:\Proposal\AI_SD-WAN` และแล็บเดิมใน WSL `AI-SDWAN-Lab` ที่ `/opt/ai-sdwan-lab` คำสั่งนี้ตรวจจากสคริปต์ปัจจุบัน ยังไม่ได้สั่งรันการสาธิตในรอบที่จัดทำคู่มือนี้

ลำดับที่แนะนำคือ เปิดระบบและโมเดลทดลอง ดูสถานะใน Grafana สาธิตโหลดจริง 210 วินาที แล้วหยุด workload ให้คืนค่าครบก่อนสาธิต WAN1 Down อีกหนึ่งรอบ ระหว่าง Down ใช้ ping อีกหน้าต่างเพื่อดูการรับส่งจริง

## 1. เตรียม PowerShell

รันบล็อกนี้ก่อนในหน้าต่างหลัก ฟังก์ชันช่วยย่อคำสั่ง WSL และตรวจว่าคำสั่งสำเร็จก่อนดำเนินต่อ

```powershell
Set-Location E:\Proposal\AI_SD-WAN

function Invoke-LabPython {
    & wsl.exe --distribution AI-SDWAN-Lab --user root `
        --cd /opt/ai-sdwan-lab --exec .venv/bin/python @args
    if ($LASTEXITCODE -ne 0) { throw 'Lab command failed; inspect the output.' }
}

function Stop-DemoWanLoads {
    Invoke-LabPython -m lab.real_traffic stop
    $demoStopDeadline = (Get-Date).AddSeconds(60)
    do {
        $demoWorkload = Invoke-LabPython -m lab.real_traffic status | ConvertFrom-Json
        if (-not $demoWorkload.active -and -not $demoWorkload.starting) {
            if ($demoWorkload.cleanup_errors) { throw 'Real traffic cleanup failed.' }
            if ($demoWorkload.PSObject.Properties.Name -contains 'restored_baseline') {
                if (-not $demoWorkload.restored_baseline) { throw 'Baseline not restored.' }
            }
            break
        }
        if ((Get-Date) -gt $demoStopDeadline) { throw 'Workload is still stopping.' }
        Start-Sleep -Seconds 2
    } while ($true)

    Invoke-LabPython -m lab.natural_wan stop
    $demoNatural = Invoke-LabPython -m lab.natural_wan status | ConvertFrom-Json
    if ($demoNatural.active) { throw 'Natural WAN is still active.' }
    if ($demoNatural.PSObject.Properties.Name -contains 'restored_baseline') {
        if (-not $demoNatural.restored_baseline) { throw 'Natural baseline not restored.' }
    }
}
```

## 2. เปิดแล็บและ API พร้อม ML ทดลอง

บล็อกนี้เริ่มรอบสาธิตใหม่ หยุด workload และ API เดิมก่อนซิงก์เวลา แล้วเปิด API ใน stream ใหม่ ข้อมูลรอบเดิมยังอยู่

```powershell
.\scripts\start-wsl-lab.ps1
Stop-DemoWanLoads

& wsl.exe --distribution AI-SDWAN-Lab --user root `
    --cd /opt/ai-sdwan-lab --exec test -f lab/runtime/live-dashboard.pid
if ($LASTEXITCODE -eq 0) {
    Invoke-LabPython scripts/serve_live_lab.py --stop
} elseif ($LASTEXITCODE -ne 1) {
    throw 'Could not check the existing API PID file.'
}

$demoStamp = Get-Date -Format yyyyMMdd-HHmmss
$demoStream = "live-demo-$demoStamp"

Invoke-LabPython scripts/sync_edge_clocks.py --apply --force-sync `
    --output "reports/flexiwan/demo-$demoStamp-clock.json"
Invoke-LabPython scripts/configure_lab_ospf.py --apply `
    --output "reports/flexiwan/demo-$demoStamp-ospf.json"

Invoke-LabPython scripts/serve_live_lab.py --background `
    --stream $demoStream --experimental-synthetic-forecast

$demoApiReady = $false
for ($demoAttempt = 0; $demoAttempt -lt 30; $demoAttempt++) {
    try {
        $demoHealth = Invoke-RestMethod https://localhost:8000/health -TimeoutSec 5
        if ($demoHealth.status -eq 'ok') { $demoApiReady = $true; break }
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $demoApiReady) { throw 'API did not become ready; inspect the API log.' }
$demoHealth
```

`model_loaded = true` หมายถึงโหลดโมเดลสำเร็จ การเปิด ML และการเปิด Auto เป็นคนละการตั้งค่า หากต้องการรอบที่ปิด ML ให้ใช้คำสั่ง launcher เดิมโดยละ `--experimental-synthetic-forecast` หลังหยุด workload/API ตามขั้นตอนเดิม

## 3. เชื่อม API และเปิด Auto / Voice

อ่าน token จากไฟล์ในเครื่องโดยไม่แสดงค่าบนหน้าจอ ใช้ HTTPS ที่ตรวจใบรับรองของแล็บ

```powershell
$demoApi = 'https://localhost:8000'
$demoToken = (Get-Content -LiteralPath `
    'lab/runtime/observability/api-token.txt' -Raw).Trim()
$demoHeaders = @{ Authorization = "Bearer $demoToken" }

function Get-DemoState {
    Invoke-RestMethod "$demoApi/api/state" -Headers $demoHeaders -TimeoutSec 15
}

function Set-DemoConfiguration {
    param([string]$Mode, [string]$Profile = 'voice')
    $demoBody = @{ mode = $Mode; profile = $Profile } | ConvertTo-Json -Compress
    Invoke-RestMethod "$demoApi/api/configuration" -Headers $demoHeaders `
        -Method Post -ContentType 'application/json' -Body $demoBody -TimeoutSec 30
}

# ตรวจข้อมูลสดก่อนเปิด Auto
$demoState = Get-DemoState
$demoState | Select-Object mode, profile, confirmed_path, model_loaded, network_available
$demoState.telemetry | ConvertTo-Json -Depth 6

# ให้ครบสาม WAN มีข้อมูล fresh และไม่มี apply_inflight ก่อนดำเนินต่อ
if ($demoState.apply_inflight) { throw 'Wait for the existing policy apply to finish.' }
foreach ($demoWan in @('wan1', 'wan2', 'wan3')) {
    $demoSample = $demoState.telemetry.$demoWan
    if (-not $demoSample -or -not $demoSample.fresh -or -not $demoSample.up) {
        throw "WAN baseline not ready: $demoWan. Wait for fresh probes, then retry."
    }
}
Set-DemoConfiguration -Mode auto -Profile voice
```

API ใหม่ต้องใช้เวลารวบรวมประวัติสำหรับ forecast โดยดูว่าตาราง ML มีค่าจริงก่อนเริ่มอธิบายการคาดการณ์ History ใช้ 12 samples ที่ cadence 5 วินาที และคาดการณ์ล่วงหน้า 30 วินาที

## 4. เปิดหน้าที่ใช้แสดง

```powershell
Start-Process 'https://localhost:3000/d/ai-sdwan?var-stream=__latest_live__&from=now-15m&to=now'
Start-Process 'https://localhost:8000/docs'
Start-Process 'https://localhost:18443/devices'
Start-Process 'https://localhost:18443/tunnels'
Start-Process 'http://localhost:18081/'
Start-Process 'http://localhost:18082/'
```

Grafana เลือก **Latest live** และ refresh 5 วินาที แสดง WAN health, Configured priority, Control mode, Application profile, ML model active, Latest decision and reason, ML forecasts และ Decision and approval audit บัญชี Grafana อยู่ใน `lab/runtime/observability/grafana-login.json`; บัญชี GUI อยู่ในไฟล์ runtime ที่ระบุใน README

หน้า Devices/Tunnels เป็น Community frontend ที่เชื่อม flexiManage backend ในโหมดอ่านข้อมูล ส่วนสองพอร์ต 18081/18082 เป็น WebUI ของ flexiEdge

## 5. สาธิตทราฟฟิกจริงและโหลดพื้นหลัง

```powershell
Stop-DemoWanLoads
Invoke-LabPython -m lab.real_traffic plan
Invoke-LabPython -m lab.real_traffic start --background --duration 210

# ใช้ดูสถานะระหว่างสาธิต
Invoke-LabPython -m lab.real_traffic status
Get-DemoState | Select-Object mode, profile, confirmed_path, model_loaded, apply_inflight
```

หนึ่งรอบมี baseline 60 วินาที, WAN1 busy 90 วินาที และ recovery 60 วินาที เปิดดู Endpoint HTTP response time, download goodput, UDP loss/RTT, Per-WAN background load และตารางผล endpoint พร้อมเวลา decision/verified apply

WAN1 ขอโหลดต่อฝั่ง 15, 110 และ 15 Mbps ตาม phase บนคอขวด 100 Mbps; WAN2 คง 16 Mbps และ WAN3 คง 3 Mbps อัตรารับจริงต้องอ่านจาก receiver ระบบอาจเลือก WAN2 หรือ WAN3 ตามข้อมูลที่วัดในรอบนั้น

หากต้องการวน phases ต่อเนื่อง ใช้คำสั่งนี้แทนการเริ่มรอบ 210 วินาที:

```powershell
Invoke-LabPython -m lab.real_traffic start --background --duration 0
```

หยุดและรอคืนค่าให้ครบก่อนสาธิตรอบอื่น:

```powershell
Stop-DemoWanLoads
Invoke-LabPython -m lab.real_traffic status
```

จบรอบหรือหยุดแล้วต้องเห็น `active=false`, `starting=false`, `restored_baseline=true` และ `cleanup_errors=[]` ตัว runner คืน classifier, mode/profile และ CONTROL baseline แต่คง priority ที่ยืนยันล่าสุด ไม่บังคับกลับ WAN1

## 6. สาธิต WAN1 Down และคืนลิงก์

หยุด real traffic/natural WAN ก่อน เพราะทั้งสองถือ shaping lock ขณะทำงาน คำสั่ง inject fault จะถูกปฏิเสธหากยังมี runner ควบคุม WAN อยู่

เปิด PowerShell อีกหน้าต่างแล้วรัน ping ผ่าน LAN tunnel:

```powershell
wsl.exe --distribution AI-SDWAN-Lab --user root `
    --cd /opt/ai-sdwan-lab --exec ip netns exec sdw-client ping -O 10.30.0.2
```

กลับหน้าต่างหลัก รันบล็อกนี้ คำสั่งรอให้กด Enter แล้วคืน CONTROL ใน `finally`:

```powershell
Stop-DemoWanLoads
Set-DemoConfiguration -Mode auto -Profile voice
Get-DemoState | Select-Object confirmed_path, model_loaded
Invoke-LabPython -m lab.impairments plan --scenario S01

try {
    Invoke-LabPython -m lab.impairments apply --scenario S01
    Write-Host 'WAN1 Down. Observe Grafana and ping; press Enter to restore.'
    Read-Host | Out-Null
} finally {
    Invoke-LabPython -m lab.impairments reset
}

Get-DemoState | Select-Object mode, confirmed_path, network_available, model_loaded
```

S01 ใส่ packet loss 100% ที่ WAN1 ทั้งสองทิศทาง ให้สังเกต health เปลี่ยนเป็น DOWN, priority ของเส้นที่ยังใช้งานได้, decision reason และการรับส่งหลัง failover กด Enter เพื่อคืนลิงก์ และกด Ctrl+C ในหน้าต่าง ping เมื่อดูผลเสร็จ

ก่อนเริ่มดู `confirmed_path` หากเส้นปัจจุบันเป็น WAN2/WAN3 การตัด WAN1 เป็นการทดสอบ standby และอาจไม่มีการเปลี่ยน priority หากต้องการตัดเส้นที่ใช้อยู่ เลือก S02 สำหรับ WAN2 หรือ S03 สำหรับ WAN3 โดยแก้ทั้ง `plan` และ `apply` ในบล็อกเดิม

คืนสภาพจาก fault ที่ใส่ด้วย CLI ด้วยคำสั่งนี้ได้เมื่อไม่มี workload runner ถือ lock:

```powershell
Invoke-LabPython -m lab.impairments reset
```

การ Down ฉับพลันใช้กฎ down/stale และ flexiWAN อาจ failover ก่อน controller ยืนยัน policy การสาธิตนี้แสดงการทำงานของระบบและการกลับมาของ forecast แต่ยังไม่พิสูจน์ว่า ML ทำนายเหตุล่มล่วงหน้า `Configured priority` เป็นค่าตั้งที่ยืนยันกลับจาก agents; ใช้ PCAP ร่วมกับ endpoint หากต้องระบุเส้นที่ส่งแพ็กเก็ตจริง

## 7. เปลี่ยนเหตุการณ์ทดสอบ

ดู catalogue ปัจจุบันครบ 38 cases:

```powershell
Invoke-LabPython -m sdwan scenarios --suite balanced
```

สำหรับ fault ของลิงก์ที่อยู่ในตารางด้านล่าง เลือกหนึ่ง ID ไปใช้ใน `plan/apply --scenario <ID>` และ `reset` หลังดูผลแต่ละรอบ:

| ID | เหตุการณ์ |
|---|---|
| CONTROL | ทุกเส้นปกติ |
| S01 | WAN1 Down |
| S02 | WAN2 Down |
| S03 | WAN3 Down |
| S04 | WAN1 และ WAN2 Down |
| S05 | WAN1 และ WAN3 Down |
| S06 | WAN2 และ WAN3 Down |
| S07 | ทุก WAN Down; ควรแสดงไม่มีเส้นทางที่ใช้ได้ |
| S08 | WAN1 หน่วงสูง |
| S09 | WAN1 jitter สูง |
| S10 | WAN1 packet loss 8% |

ตัวอย่างทดสอบคุณภาพที่ลดลง:

```powershell
Stop-DemoWanLoads
try {
    Invoke-LabPython -m lab.impairments apply --scenario S08
    Read-Host 'Observe measured RTT, forecasts and the decision; Enter to restore' | Out-Null
} finally {
    Invoke-LabPython -m lab.impairments reset
}
```

S12/S13 เป็นเหตุการณ์ที่เปลี่ยนตามเวลา `apply` ครั้งเดียวเป็นเพียง snapshot; ต้องกำหนด `--elapsed` และปรับซ้ำหากต้องการ ramp/flap ส่วน S11 ใน `impairments apply` เพียงอย่างเดียวไม่สร้าง background sender ให้ใช้ real traffic runner หรือ live evaluator สำหรับโหลดจริง

Catalogue มีทั้ง fault ของลิงก์และกรณีข้อมูลวัดผิดปกติ จึงไม่ได้ฉีดได้ครบทุกกรณีด้วย `impairments apply` เพียงคำสั่งเดียว ตัวอย่างเช่น S15 ไม่ได้สร้าง missing/stale telemetry และ S16 จะเพิ่ม latency แทนการทำ telemetry corrupt ให้ใช้ live evaluator สำหรับกรณีทดสอบข้อมูลวัดเหล่านี้

## 8. Manual และ Approve / Reject

```powershell
Set-DemoConfiguration -Mode manual -Profile voice
```

ใส่เหตุการณ์ด้วยบล็อกข้อ 7 แล้วเปิด Control and approvals ใน Grafana เมื่อมี Pending recommendation ให้กด Approve หรือ Reject หรือใช้ API ด้านล่าง โดยอ่าน ID ใหม่ก่อนสั่งทุกครั้ง หากใช้บล็อก WAN Down ในข้อ 6 ให้ข้ามบรรทัด `Set-DemoConfiguration -Mode auto -Profile voice` เพื่อคงโหมด Manual

Approve:

```powershell
$demoPending = (Get-DemoState).pending
if (-not $demoPending) { throw 'No pending recommendation.' }
Invoke-RestMethod "$demoApi/api/recommendations/$($demoPending.id)" `
    -Headers $demoHeaders -Method Post -ContentType 'application/json' `
    -Body '{"action":"approve"}' -TimeoutSec 30
```

Reject เป็นอีกทางเลือกหนึ่ง ใช้กับคำแนะนำที่ยังรออยู่:

```powershell
$demoPending = (Get-DemoState).pending
if (-not $demoPending) { throw 'No pending recommendation.' }
Invoke-RestMethod "$demoApi/api/recommendations/$($demoPending.id)" `
    -Headers $demoHeaders -Method Post -ContentType 'application/json' `
    -Body '{"action":"reject"}' -TimeoutSec 30
```

คำแนะนำหมดอายุหรือสภาพ WAN เปลี่ยนแล้ว API อาจคืน 409 ให้ดู state ใหม่ ถ้า Approve คืน 202 หมายถึงเริ่ม apply แล้ว ให้รอ `apply_inflight=null` และตรวจ audit/priority อีกครั้ง

เปลี่ยน profile หรือกลับ Auto:

```powershell
Set-DemoConfiguration -Mode manual -Profile interactive
Set-DemoConfiguration -Mode manual -Profile bulk
Set-DemoConfiguration -Mode auto -Profile voice
```

## 9. Natural WAN: ไฟเบอร์ / บรอดแบนด์ / มือถือ

ใช้แทนรอบ real traffic หรือ manual fault เพื่อแสดงค่าที่เปลี่ยนต่อเนื่องของ WAN ทั้งสาม:

```powershell
Stop-DemoWanLoads
Invoke-LabPython -m lab.natural_wan plan
Invoke-LabPython -m lab.natural_wan start --background --duration 180
Invoke-LabPython -m lab.natural_wan status
```

หากต้องการเปิดต่อเนื่องใช้ `start --background` โดยละ `--duration 180` หยุดด้วย `Stop-DemoWanLoads` โหมดนี้เพิ่ม delay/jitter/burst loss ตามโปรไฟล์สมมติ ส่วน real traffic demo ในข้อ 5 เอา impairment เหล่านี้ออกระหว่างรันและใช้คอขวด 100/80/30 Mbps

## 10. อ่านสถานะและ log

```powershell
Invoke-RestMethod "$demoApi/health"
$demoState = Get-DemoState
$demoState | Select-Object mode, profile, confirmed_path, model_loaded, network_available, apply_inflight
$demoState.last_decision | ConvertTo-Json -Depth 10
$demoState.pending | ConvertTo-Json -Depth 8
(Invoke-RestMethod "$demoApi/api/events" -Headers $demoHeaders) |
    Select-Object -Last 15 | ConvertTo-Json -Depth 10

wsl.exe --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab `
    --exec tail -n 60 lab/runtime/logs/live-dashboard.log
wsl.exe --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab `
    --exec tail -n 60 lab/runtime/logs/real-traffic.log
```

`verify_stack.py` ตรวจ baseline Manual ที่ปิด ML จึงไม่ใช้เป็น health check ของ Auto + ML demo ใช้ health/state และ readback/audit ที่แสดงใน Grafana ข้างต้น

## 11. จบสาธิต

คง dashboard/collector ทำงานใน Manual:

```powershell
Stop-DemoWanLoads
Invoke-LabPython -m lab.impairments reset
Set-DemoConfiguration -Mode manual -Profile voice
Get-DemoState | Select-Object mode, confirmed_path, model_loaded, apply_inflight
```

หากต้องการหยุด API ด้วย รันเพิ่มหลัง workload คืนค่าครบ:

```powershell
Invoke-LabPython scripts/serve_live_lab.py --stop
```

คำสั่งนี้ยังคง VMs, controller, TimescaleDB และ Grafana ของแล็บไว้

## 12. Benchmark แยกจากการสาธิตสั้น

ตรวจแผน balanced โดยไม่รัน faults:

```powershell
Invoke-LabPython -m lab.run_live_eval --suite balanced `
    --profiles voice --strategies reactive --repetitions 1 --plan
```

หากต้องการทดลอง S01 จริงครบสี่วิธี วิธีละสามรอบ รวม 12 trials ให้หยุด workload และ API ก่อน แล้วใช้ชื่อผลลัพธ์ใหม่:

```powershell
Stop-DemoWanLoads
Invoke-LabPython scripts/serve_live_lab.py --stop
$demoBenchmarkStamp = Get-Date -Format yyyyMMdd-HHmmss
.\scripts\run-wsl-experiment.ps1 -OutputName "live-demo-s01-$demoBenchmarkStamp" `
    -Scenarios S01 -Profiles voice `
    -Strategies native_priority,native_qbr,reactive,predictive_synthetic_lab `
    -Repetitions 3
```

Benchmark ใช้เวลานานกว่า demo และมี policy writer ของตัวเอง จบแล้วเปิด live API ใหม่ตามข้อ 2–3 หากต้องการดู live ต่อ ผล simulation, packet experiments และ forecast evaluation ต้องอธิบายแยกกัน

## ขอบเขตที่ใช้พูดประกอบการสาธิต

- ทราฟฟิกส่งจริงระหว่างสองไซต์ แต่ WAN ยังเป็นเส้นเสมือนในแล็บ ความจุ 100/80/30 Mbps
- XGBoost คาดการณ์ metrics; policy ตรวจ SLA, freshness และเงื่อนไขสลับเส้นก่อนสั่ง controller
- ผล ML ในข้อมูลจริงยังไม่ผ่าน acceptance จึงเปิดด้วย flag experimental และไม่ได้ promote candidate ใหม่
- การเห็น WAN Down แล้วเส้นอื่นรับงานแสดง failover ได้ ต้องเทียบกับ reactive/native และวัดผลปลายทางก่อนอ้างว่า ML เพิ่มประสิทธิภาพ

แหล่งคำสั่ง: `scripts/start-wsl-lab.ps1`, `scripts/serve_live_lab.py`, `lab/real_traffic.py`, `lab/natural_wan.py`, `lab/impairments.py`, `sdwan/api.py`, `docs/REAL_TRAFFIC.md` และ `docs/NATURAL_WAN.md`
