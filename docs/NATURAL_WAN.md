# โหมด WAN ไฟเบอร์ / บรอดแบนด์ / มือถือ

โหมดนี้เปลี่ยนสภาพเครือข่ายจริงใน isolated Linux lab ด้วย NetEm และส่ง UDP background traffic จริงเข้า underlay ของแต่ละ WAN ตัว collector เดิมยังวัด ICMP และ VPP counters แล้วบันทึกลง TimescaleDB; Grafana แสดงค่าที่วัดได้ ตัวจำลองไม่เขียนค่าทดแทนลง telemetry

เปิดใช้ตามตัวเลือกของผู้ใช้เมื่อ 9 ตุลาคม 2026 ใน stream `live-natural-20261009-231927` ร่วมกับ Auto / voice และ live ML แบบ experimental แต่ละ WAN มี random generator และช่วงโหลดของตัวเอง ความเปลี่ยนแปลงของ delay, jitter และโหลดถูกปรับอย่างต่อเนื่องทุก 10 วินาที ไม่กระโดดไปค่าใหม่ทันที

## โปรไฟล์ที่ใช้

| WAN | ประเภทจำลอง | ความจุสูงสุด | Delay เริ่มต้นต่อทิศทาง | พฤติกรรม |
|---|---|---|---|---|
| WAN1 | Fiber | 100 Mbps | 12 ms | ค่อนข้างนิ่ง; calm ยาว 100–220 วินาที มี busy เป็นครั้งคราว |
| WAN2 | Broadband | 80 Mbps | 20 ms | สลับ calm/busy; busy เพิ่ม delay เป้าหมาย 20 ms และเพิ่ม jitter/โหลด |
| WAN3 | Mobile | 30 Mbps | 32.5 ms | เปลี่ยนช่วงเร็วกว่า; มี calm/busy/fade โดย fade เพิ่ม delay เป้าหมาย 42 ms และ burst loss |

ช่วงโหลดที่ตั้งไว้เป็นเปอร์เซ็นต์ของความจุ: WAN1 calm 6–12%, busy 12–18%; WAN2 calm 10–18%, busy 22–36%; WAN3 calm 8–18%, busy 18–32%, fade 24–40% ค่าถูก smooth ก่อนส่งจริง ปริมาณที่ VPP วัดอาจต่างจากเป้าหมายเพราะ scheduling, headers, packet loss และหน้าต่างวัด

Nominal rate คง 100/80/30 Mbps เพื่อให้ denominator ของ utilization และ headroom ตรงกับ collector เดิม คุณภาพที่เปลี่ยนคือ delay, jitter, burst loss และ background load ไม่ได้จำลองการเปลี่ยนความจุวิทยุเต็มรูปแบบ

Delay ใช้ normal distribution และ correlation 20/35/50% ตามลำดับ ส่วน loss ใช้ Gilbert–Elliott (`gemodel`) เพื่อให้มีช่วงสูญเสียติดกัน พารามิเตอร์แต่ละช่วงอยู่ใน `PROFILES` ของ [lab/natural_wan.py](../lab/natural_wan.py) เป็นสมมติฐานสำหรับ lab ที่ยังไม่ได้ calibrate กับ trace จาก ISP จริง ไม่ใช่ตัวเลขมาตรฐานของไฟเบอร์ บรอดแบนด์ หรือเครือข่ายมือถือ ดูรูปแบบคำสั่งและข้อจำกัดของ kernel timer ใน [tc-netem manual](https://man7.org/linux/man-pages/man8/tc-netem.8.html)

## เปิด ดูสถานะ และหยุด

เตรียม lab, edges และ decision API ให้ทำงานก่อนตาม [REAL_TESTBED](REAL_TESTBED.md) หากเริ่ม API ใหม่ให้หยุด natural mode ก่อน แล้วค่อยเปิดใหม่หลัง API ได้ probe สดครบ 3 WAN เพื่อให้ข้อมูล environment ผูกกับ stream ปัจจุบัน การเปิด natural mode ไม่เปลี่ยน Auto/Manual, application profile, policy หรือเปิด ML ให้อัตโนมัติ

PowerShell บนเครื่องนี้:

```powershell
# เลือก stream ของ decision API ที่กำลังทำงานโดยอัตโนมัติ
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python -m lab.natural_wan start --background
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python -m lab.natural_wan status

# หยุดโหลดทั้งสามเส้นและคืน CONTROL baseline
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python -m lab.natural_wan stop
```

Linux ใน prepared owned lab:

```bash
sudo .venv/bin/python -m lab.natural_wan start --background
sudo .venv/bin/python -m lab.natural_wan status
sudo .venv/bin/python -m lab.natural_wan stop
```

ใช้ `--stream <ชื่อปัจจุบัน>` เพื่อตรวจชื่อให้ตรงแบบ explicit, `--seed 20261009` เพื่อเลือก seed หรือ `--duration 180` เพื่อให้หยุดและคืน baseline หลัง 180 วินาที ค่าเริ่มต้นรันต่อจนสั่ง stop ใช้ `python -m lab.natural_wan plan` ดูแผนบน Windows ได้โดยไม่แก้เครือข่าย Seed ทำให้ลำดับโมเดลเหมือนกันเมื่อป้อนเวลาเดียวกัน; ไม่รับประกัน packet trace เดียวกัน เพราะเวลาของ process และการสุ่มใน kernel ยังมีผล

ใน [Grafana](https://localhost:3000/d/ai-sdwan?var-stream=__latest_live__&from=now-15m&to=now) เลือก **Latest live** การ์ด **WAN environment (lab profiles)** แสดง `Natural WAN: Fiber / Broadband / Mobile` ขณะ heartbeat ของตัวจำลองสด หากขึ้น status stale ให้ตรวจ runner/log ก่อนสรุปว่าคืน baseline แล้ว ปุ่ม **Control and approvals** ยังใช้เปลี่ยน mode/profile และอนุมัติคำแนะนำตามสิทธิ์เดิม การเปิด–หยุด environment ใช้ CLI ด้านบน

## การคืนค่าและการทดลองเดิม

ตัวจำลองต้องใช้ root และ marker `.ai-sdwan-owned` ตรวจ VM identity, topology และ qdisc baseline ที่รู้จักครบหก interfaces ก่อนเริ่ม เปลี่ยนเฉพาะ interfaces `a/b` ใน namespaces `sdw-wan1/2/3` เท่านั้น ใช้ `tc qdisc change` เพื่อเก็บ qdisc เดิมและ counters ต่อเนื่อง

Natural mode ถือ file lock ตลอดการทำงาน `lab.impairments apply/reset` และ live runner ปฏิเสธเมื่อ lock ถูกใช้อยู่ ให้สั่ง **stop natural mode ก่อนหยุด API และเริ่ม packet benchmark** การคืน CONTROL baseline คือ delay 12/20/32.5 ms, jitter 2.8/5/7 ms ต่อทิศทาง, loss 0% และ rate 100/80/30 Mbps ตามเดิม

เมื่อรับ SIGTERM/SIGINT หรือพบ load worker หยุด ตัว runner หยุด workers และพยายามคืน baseline ใน `finally` จากนั้นบันทึกผล การสั่ง stop ซ้ำไม่ส่งสัญญาณไป PID เดิมอีก หาก runner ถูก kill แบบไม่มี cleanup ให้สั่ง stop เพื่อตรวจ PID และคืน baseline; ไม่อ้างว่า hard crash คืนค่าได้ทันที Workers หยุดเองเมื่อ parent หายหรือ environment heartbeat เกิน 25 วินาที การคืน qdisc หลัง hard crash ยังต้องใช้ stop

Runtime state และ logs อยู่ใน ignored `lab/runtime/natural-wan.json`, `lab/runtime/logs/natural-wan.log` และ `natural-wan-load.log` ฐานข้อมูลเก็บ state `wan_environment` และ events `natural_wan_started`, `natural_wan_profile`, `natural_wan_stopped` พร้อม seed, source SHA-256, profile definitions และ settings ตามเวลา ใช้ประกอบการอ่าน telemetry ย้อนหลัง

หลัง reboot ให้ resume lab/API ตามเดิมแล้วเปิด natural mode ใหม่ ไม่มีการเปิดโหมดนี้อัตโนมัติเมื่อ boot

## วิธีอ่านผล

`latency_ms` เป็น ICMP RTT mean; `jitter_ms` เป็น RTT standard deviation; loss วัดจาก 100 packets จึงละเอียดทีละ 1% การตั้ง loss ต่ำกว่า 1% อาจให้หลายรอบเป็น 0% แล้วมีบางรอบเพิ่มขึ้น Utilization ใช้ VPP byte counters ของทิศทางที่สูงกว่า หารด้วย nominal rate ไม่ใช่ผล bandwidth speed test

Background traffic ของโหมดนี้เข้า edge A ผ่านแต่ละ underlay เพื่อให้ counters มีโหลดจริง ไม่ใช่การวัด application goodput ข้าม IPsec tunnel Configured priority ยังหมายถึง agent configuration; การยืนยันเส้นทาง application ต้องใช้ UDP end-to-end/PCAP ใน runner เดิม

ผลโหมดนี้แสดงการทำงานของ emulation และระบบวัด ยังใช้ frozen synthetic model เดิมแบบ experimental และยังไม่ใช่ผล validate ความแม่นยำ ML หรือหลักฐานว่า AI ดีกว่า reactive ผล packet benchmark และ frozen artifacts เดิมเก็บแยกไว้

หลักฐานรอบเปิดใช้: [activation](../reports/flexiwan/natural-wan-live-natural-20261009-231927-activation.json), [start/stop/restore lifecycle](../reports/flexiwan/natural-wan-live-natural-20261009-231927-lifecycle.json), [ค่าที่วัดและ settings](../reports/flexiwan/natural-wan-live-natural-20261009-231927-measurements.json), [Grafana SQL panels](../reports/flexiwan/natural-wan-live-natural-20261009-231927-grafana.json), [readiness ของ source รุ่นสุดท้าย](../reports/flexiwan/natural-wan-live-natural-20261009-231927-ready.json)
