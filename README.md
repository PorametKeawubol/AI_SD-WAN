# AI SD-WAN Lab

ระบบทดลอง WAN 3 เส้น: **flexiEdge → telemetry → FastAPI/Python → SLA policy → flexiManage REST API** พร้อม TimescaleDB, Grafana และ XGBoost ตาม stack ที่กำหนด

เครื่องนี้รัน flexiManage 6.3.37 และ flexiEdge 6.3.35 สองเครื่องผ่าน KVM ใน WSL distro `AI-SDWAN-Lab` แล้ว มี IPsec tunnels 3 เส้น Hypervisor/VirtualMachinePlatform/WSL เปิดใช้งานหลัง restart สำเร็จ

## Stack ที่ใช้งาน

| ส่วนของระบบ | Technology | สถานะ |
|---|---|---|
| SD-WAN Edge | flexiEdge | 2 VMs, version 6.3.35 |
| SD-WAN Controller | flexiManage | version 6.3.37 |
| SD-WAN Platform | flexiWAN | controller + agents + 3 WAN tunnels |
| Telemetry & Decision Service | FastAPI | HTTPS API และ loop รับ probe จริง |
| Backend / Service Logic | Python | validation, decision, approval, apply/readback |
| Database / Time-Series DB | TimescaleDB | 2.30.2 / PostgreSQL 17; telemetry hypertable |
| Monitoring & Dashboard | Grafana | 13.2.3; 15 panels, SQL อ่านอย่างเดียว และปุ่มควบคุมผ่าน authenticated API |
| Machine Learning | XGBoost | 3.4.1; โมเดล simulation ที่ตรึงไว้; เปิด live ML แบบทดลองตามคำสั่งผู้ใช้ 8 ต.ค. 2026 |
| Controller Integration | REST API | jobs + agent configuration readback |
| Database Communication | SQL | psycopg 3, parameterized queries |
| Communication / Security | Secure API / HTTPS | Bearer API token, HTTPS และ PostgreSQL TLS verify-full |

SQLite backend และ dashboard HTML/JS/CSS เดิมถูกถอดออกแล้ว ย้าย telemetry เดิม 6,384 records, events 2,287 records และ state 2 records เข้า TimescaleDB โดยตรวจ content hash ตรงกัน เก็บสำรองและ frozen experiment artifacts เดิมไว้ ดู [รายงานย้าย stack](reports/flexiwan/STACK_MIGRATION.md)

MongoDB/Redis ยังจำเป็นต่อ flexiManage upstream ส่วน Mailpit ใช้รับอีเมล bootstrap ภายใน lab แยกจากฐานข้อมูล telemetry ของระบบ Python

## เปิดใช้งานบนเครื่องนี้

- **Grafana:** <https://localhost:3000/d/ai-sdwan>
- **API reference:** <https://localhost:8000/docs>
- **Health:** <https://localhost:8000/health>

Grafana มี panel **Control and approvals** ใต้แถวสถานะ ใช้ปุ่ม **Auto / Manual**, เลือก **Voice / Interactive / Bulk** แล้วกด **Apply profile**, หรือกด **Approve / Reject** คำแนะนำที่รออนุมัติได้ใน Dashboard โดยตรง บัญชี Grafana **Admin** มีสิทธิ์ส่งคำสั่ง ส่วน Viewer อ่านสถานะได้ โหมด **Auto** ตัดสินใจตาม SLA และเงื่อนไขของ policy เอง; โหมด **Manual** ต้องอนุมัติคำแนะนำก่อนเปลี่ยนเส้นทาง ถ้าไม่มีคำแนะนำ ปุ่ม Approve/Reject จะปิดไว้

เลือก **Data stream = Latest live** เพื่อควบคุมระบบปัจจุบัน เมื่อดู archive ปุ่มจะปิดไว้ API ยังตรวจ recommendation ID, อายุคำแนะนำ และสภาพเครือข่ายก่อน apply และรอ controller/agent readback การ์ดสถานะรองรับข้อความ `auto`, `voice`, `wan1` และ `true` แล้ว กด **Ctrl+F5** หากแท็บเดิมยังแสดง Dashboard รุ่นเก่า ดู [รายละเอียด control plugin](lab/grafana/control-plugin/README.md)

บัญชี Grafana อยู่ใน ignored `lab/runtime/observability/grafana-login.json` บน Windows ปุ่มใน Grafana ใช้ session ที่ล็อกอินอยู่ และให้เซิร์ฟเวอร์เติม API token จาก encrypted plugin settings โดยไม่ส่ง token ไปยัง JavaScript หรือ dashboard JSON หากใช้ Swagger โดยตรง API token อยู่ใน ignored `lab/runtime/observability/api-token.txt` กด **Authorize** แล้วใส่ token; Swagger ไม่บันทึกข้ามการโหลดหน้า CA ของ lab ติดตั้งใน Windows CurrentUser Root แล้ว API/Grafana ยืนยันใบรับรองได้ผ่าน Windows HTTPS client

สถานะหลัง migration เป็น **Manual / voice / configured priority WAN2** และรับ probe จริงครบ 3 WAN ตรวจ query ของ Grafana ทั้ง 14 SQL panels, datasource อ่านอย่างเดียว, agent configuration ทั้งสอง edges และ LAN ping 3/3 แล้ว Browser ของเครื่องมือตรวจภาพยังไม่เชื่อถือ CA จึงยังไม่ได้ตรวจหน้าจอแบบ interactive

สถานะล่าสุด 9 ตุลาคม 2026 หลัง resume: **Auto + live ML แบบ experimental / voice / configured priority WAN1** มีคำทำนายจาก probe จริงครบ 3 WAN ใน stream `live-auto-20261009-201515` และ [monitor จาก Windows](reports/flexiwan/resume-20261009-201515-windows-monitor-followup.json) ผ่าน 13/13 ตัวอย่างใน 62 วินาที [ผลตรวจ stack ซ้ำ](reports/flexiwan/resume-20261009-201515-verification-followup.json) ยืนยัน agents ทั้งสอง, tunnels 3 เส้น, SQL/Grafana, LAN ping 3/3 และ unresolved intents เป็น 0 ค่า WAN1 อ่านได้ตั้งแต่ก่อนเปิด Auto รอบนี้; decision ล่าสุดเป็น `hold` เพราะ current path remains preferred และ stream ใหม่ยังไม่มี event `applied` โมเดลใน Linux ใช้ weights เดิมและยังไม่ได้ validate ประสิทธิภาพ ML บนข้อมูลจริง

ดูข้อมูลสดที่ [Grafana Auto + ML](https://localhost:3000/d/ai-sdwan?var-stream=__latest_live__&from=now-1h&to=now) หน้าหลักเลือก **Data stream = Latest live** และติดตามรอบ live ล่าสุดโดยอัตโนมัติ ชื่อ stream ของแต่ละรอบยังเลือกได้เพื่อดูประวัติ หากแท็บเก่าขึ้น No data ให้เปิดลิงก์นี้เพื่อคืนทั้ง stream และช่วงเวลาเป็นข้อมูลสด ข้อมูลรอบก่อนยังอยู่ใน stream เดิม รวม `live-auto-20261008-162832` และ `live` รอบนี้ตรวจเวลา WSL ตรงกับ Windows และ sync edges ก่อนเปิด API; stream ใหม่มี future telemetry rows เป็น 0 NTP ของเฉพาะ lab ยังปิดอยู่; หลัง suspend/restart ต้องตรวจ clock ใหม่ก่อนเปิด decision API

หลัง reboot เปิด lab เดิมด้วย PowerShell:

```powershell
Set-Location E:\Proposal\AI_SD-WAN
.\scripts\start-wsl-lab.ps1
# รอให้สอง edges reconnect; API/runner ต้องหยุดก่อนตรวจ clock และ timers
$resumeStamp = Get-Date -Format yyyyMMdd-HHmmss
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/sync_edge_clocks.py --apply --force-sync --output "reports/flexiwan/resume-$resumeStamp-clock.json"
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/configure_lab_ospf.py --apply --output "reports/flexiwan/resume-$resumeStamp-ospf.json"
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/serve_live_lab.py --background
Invoke-RestMethod https://localhost:8000/health
```

`start-wsl-lab.ps1` เปิด WSL keepalive แบบซ่อนและ resume network/controller/VM รวม TimescaleDB/Grafana เดิมผ่าน native Docker endpoint `unix:///run/ai-sdwan-docker.sock` ตรวจ identity ก่อนเปิด resource ไม่สร้าง credentials/VM disks ใหม่ หาก API เปิดอยู่แล้วไม่ต้องเปิดซ้ำ การตรวจ clock/timers ต้องหยุด API ก่อน

หากต้องการเปิด live ML แบบทดลองหลัง restart ให้เติม `--experimental-synthetic-forecast` ในคำสั่ง `scripts/serve_live_lab.py --background` ตัว launcher ส่ง flag ไปยัง background worker ด้วย ต้องหยุด API เดิมด้วย `scripts/serve_live_lab.py --stop` ก่อนเริ่มใหม่ ค่าเริ่มต้นเมื่อไม่ส่ง flag ยังคงปิด live ML

Launcher รองรับ `--stream <ชื่อใหม่>` เพื่อแยก telemetry/state เช่น `--background --stream live-auto-ชื่อรอบ --experimental-synthetic-forecast` การเริ่ม live API ยังคงตั้ง Manual; เปิด Auto ผ่าน `POST /api/configuration` ด้วย `{"mode":"auto"}` หลังตรวจข้อมูลสดและ readback แล้ว การเปิด ML กับการเปิด Auto เป็นคนละการตั้งค่า

ตรวจระบบ baseline ที่ปิด live ML แบบอ่านกลับและสร้างหลักฐานใหม่ใน Linux:

```bash
.venv/bin/python scripts/verify_stack.py --output reports/flexiwan/stack-readback-new.json
```

## Python และ simulation

ใช้ CPython 3.12 ขึ้นไป ติดตั้ง dependencies และรัน offline simulation โดยไม่ต้องเปิด live API:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install -e ".[dev,lab]"
.\.venv\Scripts\python.exe -m sdwan scenarios
.\.venv\Scripts\python.exe -m sdwan report
```

มี frozen model/results ใน workspace แล้ว `python -m sdwan train` และ `eval --suite balanced --repetitions 30 --seed-base 70000 --output reports/balanced-new` ใช้สร้างผล simulation ใหม่ ควรเลือก output ใหม่เพื่อรักษาหลักฐานเดิม Live ML ปิดเป็นค่าเริ่มต้นเพราะยังไม่ได้ validate โมเดล synthetic กับ probe จริง

Git repository มี source, dashboard/plugin ที่ build แล้ว และรายงานผล ไม่รวม credentials, runtime, virtual environment, vendor, model weights, raw PCAP, SQLite backups หรือ experiment logs ไฟล์เหล่านี้ยังเก็บไว้ใน workspace เดิม หาก clone ไปเครื่องใหม่ให้สร้างโมเดลด้วย `python -m sdwan train` และเตรียม private runtime ตาม [REAL_TESTBED](docs/REAL_TESTBED.md)

หากเปิด simulation API ให้ใช้ TimescaleDB เช่นเดียวกัน ตั้ง `SDWAN_DATABASE_URL`, `SDWAN_API_TOKEN`, `SDWAN_TLS_CERT`, `SDWAN_TLS_KEY`, `SDWAN_CA_FILE` จาก runtime ของ deployment และเลือก `serve --stream simulation-ชื่อใหม่` ใช้หนึ่ง process/worker เท่านั้น Replay ใช้ HTTPS และ CA ที่ตรวจได้; live API ปฏิเสธ synthetic injection

```bash
python -m sdwan serve --stream simulation-new
python -m sdwan replay --scenario S12 --url https://localhost:8000 --ca-file /path/to/lab-ca.crt
```

SQL integration tests ต้องตั้ง `SDWAN_DATABASE_URL` ของ lab; หากไม่ได้ตั้งจะ skip เฉพาะกลุ่มนั้น ผลหลัง migration ผ่าน **146 tests ทั้ง Windows และ Linux** โดยเชื่อม TimescaleDB จริง หลักฐานอยู่ใน [รายงาน migration](reports/flexiwan/STACK_MIGRATION.md)

## ชุดทดสอบ 3 WAN ที่ปรับใหม่

ใช้ balanced v2 รวม **38 cases = 37 faults + 1 CONTROL**: availability8 (Up/Downครบทุกแบบ), quality15 (5ชนิด×3WAN), stability3, mixed6 และ telemetry6 จำนวนนี้เป็นชุดทดสอบที่เลือก; XGBoost ทำนาย metrics ไม่ได้จำแนก38classes

เพิ่ม target WAN2/WAN3 เพื่อให้ครอบคลุมทุกลิงก์ คง initial priority WAN1; cases ที่เสียเฉพาะ standby ทดสอบการรับมือโดยไม่เปลี่ยน path โดยไม่จำเป็นด้วย มี `--suite legacy` สำหรับนิยาม/ผลเดิม ไม่เปลี่ยน SLA gates หรือ frozen weights

```powershell
.\.venv\Scripts\python.exe -m sdwan scenarios --suite balanced
.\.venv\Scripts\python.exe -m lab.run_live_eval --suite balanced --profiles voice --strategies reactive --repetitions 1 --plan
```

Simulation smoke ใหม่ครบ570 controller trials (38×3profiles×5strategies×1seed); reactiveและguarded AIผ่าน37/37 faultsในseedนี้ ผลอยู่ใน [balanced smoke](reports/synthetic-balanced-smoke-20261002/EVALUATION.md) เป็นการตรวจ pipeline และยังไม่ใช่ packet result หรือหลักฐานว่า AIดีกว่า baseline การปรับรอบนี้ผ่าน175unit testsบนWindowsและ183testsบนLinuxรวมTimescaleDBจริง ดู [รายงานการออกแบบ](reports/flexiwan/SCENARIO_DESIGN.md) และ [protocol/การแบ่งรัน](docs/EVALUATION_PROTOCOL.md)

## Live packet experiments และผลเดิม

หยุด decision API ก่อนทดลองเพื่อให้มี policy writer เดียว Grafana เปิดอ่านข้อมูลต่อได้:

```powershell
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python scripts/serve_live_lab.py --stop
.\scripts\run-wsl-experiment.ps1 -OutputName live-s01-new -Repetitions 3
```

เลือก output ใหม่ทุกครั้ง Helper โหลด credentials ภายใน Linux ตรวจ clocks/OSPF และกัน idle sleep ชั่วคราว Runner เก็บแต่ละ trial ใน TimescaleDB stream แยก พร้อม `audit.json`, stream reference, UDP observations และ PCAP

ผล packet ก่อน migration: S01 voice ผ่าน 12/12 รอบของสี่ strategies; reactive voice มี valid observations ครบ 16 cases และผ่าน 14/16 ยังไม่ผ่าน S07 (no_path เกิน 5 วินาที) และ S12 (SLA 97.14% ต่ำกว่า 98%) ผลรวม 45 attempts ถูกเก็บไว้ครบ การย้าย stack รอบนี้ไม่ได้รันทดลอง packet grid ใหม่

ยังไม่ได้รันเต็ม legacy **576 trials** (27.2ชั่วโมง) หรือ balanced **1,368 trials** (64.6ชั่วโมง) และยังไม่ได้ validate ML บนข้อมูลจริง ผล `reports/latest` เป็น simulation ค่า capacity/headroom ของ simulator เป็น proxy; configured priority ของ live API เป็น agent configuration ต้องใช้ PCAP เพื่อยืนยัน WAN ที่ส่งแพ็กเก็ตจริง

## เอกสารและหลักฐาน

- [การย้าย stack และตรวจข้อมูล](reports/flexiwan/STACK_MIGRATION.md)
- [วิธี resume หลัง restart](reports/flexiwan/RESTART_RESUME.md)
- [รายละเอียด implementation](docs/IMPLEMENTATION.md)
- [Linux lab / live evaluation](docs/REAL_TESTBED.md)
- [ผล packet ที่ตรึงไว้](reports/flexiwan/CONTINUATION.md)
- [ผล simulation](reports/latest/EVALUATION.md) และ [เกณฑ์ evaluation](docs/EVALUATION_PROTOCOL.md)
- [บันทึกงาน](docs/WORK_LOG.md)

โครงการพัฒนาจาก `E:\Proposal\coop-proposal-Poramet-v7-9-2026.docx` โดยไม่แก้ไฟล์ proposal ต้นฉบับ ขอบเขตปัจจุบันเป็น isolated research lab, หนึ่ง policy/application profile ที่เลือก และหนึ่ง decision worker
