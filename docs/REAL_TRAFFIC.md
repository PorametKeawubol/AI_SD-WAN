# ทราฟฟิกใช้งานจริงและโหลดพื้นหลังแยก WAN

โหมดนี้ใช้แพ็กเก็ตจริงสร้างโหลดและวัดผลที่ปลายทาง โดยยังใช้ WAN เสมือนของแล็บเดิม ความจุถูกจำกัดไว้ที่ **WAN1 100 Mbps / WAN2 80 Mbps / WAN3 30 Mbps** จึงเรียกว่า **real traffic with emulated capacity** ไม่ใช่การทดสอบอินเทอร์เน็ตจากผู้ให้บริการสามราย

รอบที่ตรวจแล้ว 11 ตุลาคม 2026 เก็บ endpoint 431 แถวและ background receiver 108 แถว ยืนยันการเปลี่ยน WAN1 → WAN2 → WAN1 และคืน baseline/classification สำเร็จ การสลับแรกเกิดจาก SLA ปัจจุบันโดยไม่มี forecast ใช้ได้ ดู [ผลที่สังเกตจริงและข้อจำกัด](../reports/flexiwan/REAL_TRAFFIC_DEMO.md) และ [ตัวเลขรายรอบ](../reports/flexiwan/real-traffic-20261011-summary.json)

ทดสอบเริ่มซ้ำหลังแก้ ARP ของ receiver แล้ว รันครบอีก 210 วินาที ได้ endpoint 432 แถวและ receiver ครบทั้ง 6 bindings หลังจบยืนยัน qdisc `CONTROL` ทั้ง 6 จุด, classifier จาก agents ทั้งสอง, ไม่มี process/namespace ของ workload ค้าง และ unresolved intents เป็น 0 คง priority WAN2 ที่ยืนยันล่าสุดไว้ตามออกแบบ ดู [ผลตรวจการเริ่มซ้ำและคืนค่า](../reports/flexiwan/real-traffic-20261011-restart-verification.json) ชุดทดสอบโค้ดที่เกี่ยวข้องผ่าน 149 tests

ระหว่างรัน จะเอาการเติม delay, jitter และ loss ของ natural WAN ออก แล้วคงเฉพาะการจำกัดอัตราส่งและคิวที่มีขนาดจำกัด ผลความหน่วงและการสูญเสียที่เกิดจากโหลดต้องอ่านจากการวัดจริง ไม่เติมค่าคุณภาพ WAN ลงในฐานข้อมูล

## เส้นทางและงานที่รัน

- **โหลดพื้นหลัง:** iperf3 UDP รวม 6 streams ส่งจาก namespace กลางของแต่ละ WAN (`172.16.X.254`) ไปยัง Linux receiver namespaces ที่แล็บสร้างเอง `sdw-load1a` ถึง `sdw-load3b` (`172.16.X.101` / `172.16.X.102`) ซึ่งเชื่อมกับ bridge ฝั่ง edge A/B ใช้คอขวดและ qdisc ร่วมกับ tunnel แต่ละฝั่ง โหลดจึงตรึงกับ WAN และไม่ย้ายตาม priority ที่ AI เลือก วัดอัตรารับข้อมูล, packet loss และ jitter จาก receiver
- **HTTP request:** Client LAN `10.20.0.2` เรียก endpoint ขนาดเล็กบน Server LAN `10.30.0.2:8088` วัดเวลาตอบกลับและผลสำเร็จ
- **ดาวน์โหลด:** รับข้อมูล HTTP ผ่าน LAN tunnel และวัด received goodput โดยใช้จำนวน bytes ที่ได้รับและเวลาที่ใช้
- **UDP application:** UDP echo workload ของ `lab.traffic` ไปยัง Server LAN พอร์ต `5209` ผ่าน policy ที่ระบบควบคุม วัด goodput ของ echo ที่ Client รับกลับ, loss, mean RTT และความแกว่งของ RTT

HTTP และ UDP เป็น workload สำหรับทดสอบการรับส่ง ไม่ได้แทนพฤติกรรมของทุกแอป และ UDP นี้ไม่ใช่เครื่องกำเนิดสายสนทนา VoIP เต็มรูปแบบ UDP echo รายงาน mean RTT และ RTT standard deviation ส่วน background iperf3 รายงาน receiver jitter ตามนิยามของ iperf3 ทั้งสองค่าเป็นคนละนิยาม ไม่แปลง RTT เป็น one-way latency

runner ตั้ง classifier สำหรับ workload และใช้ application profile `voice` ระหว่างการทดลอง โค้ดปัจจุบันควบคุมหนึ่ง profile หลัก จึงไม่ได้เลือก SLA แยกให้ HTTP, download และ UDP พร้อมกัน

**โหลดพื้นหลังนี้แทน competing WAN/ISP cross-traffic และไม่เข้า VPP ของ edge** จึงไม่รวมใน `utilization_pct` จาก WAN counters ของ API ค่านั้นแสดงเฉพาะทราฟฟิกที่ edge รับส่ง ส่วน RTT/loss จาก ICMP probe และผล application สามารถสะท้อนความแออัดที่คอขวดร่วมได้ ต้องดูกราฟ per-WAN background ประกอบ ไม่ตีความ utilization ต่ำว่า WAN ไม่มีโหลดพื้นหลัง

## รอบการเพิ่มโหลด

หนึ่งรอบใช้เวลาเป้าหมาย 210 วินาที:

| Phase | ระยะเวลา | สิ่งที่ทดสอบ |
|---|---:|---|
| `baseline` | 60 วินาที | โหลดพื้นหลังเบาและวัด endpoint เริ่มต้น |
| `wan1_busy` | 90 วินาที | เพิ่มโหลดที่ตรึงกับ WAN1 แล้วสังเกตการวัดและการตัดสินใจ |
| `recovery` | 60 วินาที | ลดโหลด WAN1 กลับและติดตามการฟื้นตัว |

แผนปัจจุบันขออัตราส่ง **ต่อฝั่ง A/B** ของ WAN1 เป็น 15 → 110 → 15 Mbps ส่วน WAN2 คง 16 Mbps และ WAN3 คง 3 Mbps การขอ 110 Mbps บนคอขวด 100 Mbps ใช้สร้างความแออัด ไม่ได้หมายความว่า receiver จะรับได้ 110 Mbps ต้องอ่านอัตราส่งและอัตรารับที่วัดจริงประกอบ

ใช้ `plan` ดูอัตราส่งและแผนปัจจุบันก่อนรัน ไม่มีการกำหนดว่าระบบต้องเปลี่ยนไป WAN2 หรือ WAN3 ระบบตัดสินใจจากข้อมูลที่ collector วัดจริงตาม SLA และ mode ที่เปิดอยู่ หากเป็น Manual การเปลี่ยน priority ยังต้องอนุมัติใน Grafana หากเป็น Auto service จะส่งคำสั่งตาม policy

ช่วงเวลา phase เป็นเป้าหมายของตัว runner การเริ่ม process, การวัดที่ยังค้าง และการคืนค่าอาจทำให้เวลารวมจริงยาวกว่า 210 วินาที

## เตรียมก่อนเริ่ม

คำสั่งต่อไปนี้ใช้ **แล็บที่เตรียมไว้แล้ว** ใน WSL distro `AI-SDWAN-Lab` ที่ `/opt/ai-sdwan-lab` ต้องมี:

- flexiManage, edge A/B, LAN namespaces, tunnels และ live API ที่ทำงานอยู่ พร้อมข้อมูล WAN สด
- iperf3 บน WSL host สำหรับ receiver namespaces และโหลด UDP; ไม่ใช้ Linux sockets บน WAN IP ของ guests เพราะ VPP เป็นผู้ถือ IP เหล่านั้น
- Python environment และ private runtime ของแล็บ ซึ่งใช้เปิด SSH, verified controller/API และ TimescaleDB
- natural WAN และ live evaluation runner หยุดอยู่ เพื่อให้มีตัวควบคุมสภาพ WAN ตัวเดียว
- ไม่มี controller apply ที่ยังค้างหรือผลลัพธ์ที่ไม่แน่นอนก่อนเริ่มเปลี่ยน classifier

runner ใช้ stream ของ live API ที่เปิดอยู่ เพื่อให้ Grafana แสดง endpoint measurements คู่กับ telemetry และ decision ในช่วงเวลาเดียวกัน ไม่ต้องหยุด API เพื่อเริ่ม workload

หากเพิ่ง reboot ให้ตรวจและ sync clocks ตาม [README](../README.md) ก่อนเริ่ม API/workload ข้อมูลที่เวลาไม่ตรงทำให้การเทียบการเปลี่ยนเส้นกับผล endpoint คลาดเคลื่อนได้

## เปิด ดูสถานะ และหยุด

จาก PowerShell ใน workspace:

```powershell
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python -m lab.real_traffic plan
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python -m lab.natural_wan stop
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python -m lab.real_traffic start --background --duration 210
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python -m lab.real_traffic status
```

`--duration 210` เป็นรอบเดียวและเป็นค่าเริ่มต้น ส่วน `--duration 0` รันซ้ำตาม phases จนสั่งหยุด:

```powershell
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python -m lab.real_traffic start --background --duration 0
```

โหมดต่อเนื่องแบ่ง process ของ application เป็น segments ไม่เกิน 600 วินาที แล้วเริ่ม segment ถัดไปเมื่อทั้ง HTTP และ UDP จบ การเปลี่ยน segment อาจมีช่องว่างสั้น ๆ แต่รอบ phase 210 วินาทียังคงอ้างอิงเวลาเริ่มการวัดเดียวกัน

เริ่มเพียงหนึ่ง runner และหยุดด้วย:

```powershell
wsl --distribution AI-SDWAN-Lab --user root --cd /opt/ai-sdwan-lab --exec .venv/bin/python -m lab.real_traffic stop
```

ภายใน WSL ใช้คำสั่งสั้นเดียวกันได้ เช่น:

```bash
cd /opt/ai-sdwan-lab
.venv/bin/python -m lab.real_traffic plan
.venv/bin/python -m lab.real_traffic start --background --duration 210
.venv/bin/python -m lab.real_traffic status
.venv/bin/python -m lab.real_traffic stop
```

ตอนหยุดหรือจบรอบ runner ยุติ process ที่ตัวเองสร้าง คืน network impairment เป็น `CONTROL` baseline และคืน classifier รวมถึง mode/profile ก่อนเริ่ม ไม่บังคับ priority กลับ WAN1 แต่คงลำดับ WAN ที่ยืนยันล่าสุดไว้ การคืน `CONTROL` จึงไม่ได้หมายความว่ายังคง capacity-only environment: baseline เดิมอาจมี delay/jitter/loss ของแล็บ natural WAN ไม่เริ่มใหม่เองหลังจบ

ตรวจ `status` และผลการคืนค่าหลังหยุด หากการคืนค่าไม่สำเร็จให้ตรวจสถานะก่อนเปิด natural WAN หรือเริ่มการทดลองถัดไป

## อ่านผลใน Grafana

เปิด [AI SD-WAN dashboard](https://localhost:3000/d/ai-sdwan?var-stream=__latest_live__&from=now-15m&to=now) แล้วเลือก **Data stream = Latest live**:

- **WAN environment (lab profiles):** แสดง real traffic และ emulated capacities พร้อมสถานะ heartbeat
- **Real traffic workload session:** run ID, phase, mode, injected delay/loss, heartbeat และผลสำเร็จล่าสุดของแต่ละ workload
- **Endpoint HTTP response time / Endpoint download goodput:** เวลาตอบกลับและอัตรารับข้อมูลที่ Client ได้จริง
- **Endpoint UDP packet loss / Endpoint UDP RTT / RTT variation:** ผล UDP echo ที่ Client วัดจากข้อมูลตอบกลับ ค่า RTT ว่างเมื่อไม่มี echo ที่ใช้วัดได้
- **Per-WAN background load: offered and received:** แยกอัตราส่งที่ sender วัดได้กับ goodput ที่ receiver วัดได้ ของแต่ละ WAN และฝั่ง A/B อัตราที่ร้องขอเป็นข้อมูลอีกฟิลด์หนึ่งในผลการทดลอง
- **Endpoint measurement outcomes:** เวลา phase และผลสำเร็จหรือความล้มเหลวของแต่ละการวัด

ผลการวัดที่ล้มเหลวหรือหายไปเป็นช่องว่างในกราฟ ไม่แทนด้วยศูนย์ ตาราง outcomes ยังเก็บความล้มเหลวไว้ การ์ด heartbeat ที่ `STALE` หรือ `STOPPED` ไม่ใช่หลักฐานว่า workload ยังทำงานปกติ

เทียบกราฟ endpoint กับ **Latest decision and reason**, **Decision and approval audit** และเวลา verified apply เพื่อดูว่าแอปดีขึ้นหลังการเปลี่ยนเส้นหรือไม่ `Configured priority` ยืนยัน configuration ของ agents แต่ไม่ยืนยันว่าแพ็กเก็ตทุกชุดใช้ WAN นั้นตลอดเวลา flexiEdge อาจ failover ได้เอง หากต้องการระบุเวลาที่ทราฟฟิกย้ายเส้นให้ตรวจ packet capture เพิ่ม

## ข้อมูลที่บันทึกและขอบเขต ML

raw output และ JSONL ของแต่ละ run อยู่ใน ignored `artifacts/real-traffic/<run_id>/` ซึ่งไม่รวมใน Git บันทึก TimescaleDB ใช้ state key `application_workload` และ events `application_measurement` / `background_measurement` คู่กับ telemetry ของ live stream ไม่แทนที่ข้อมูล collector

packet capture เก็บเฉพาะช่วงต้นของแพ็กเก็ตและหมุนไฟล์เป็น ring **3 ไฟล์ × ประมาณ 16 MB ต่อ WAN** รวมประมาณ 144 MB ทั้งสาม WAN โดยเขียนทับไฟล์เก่า raw JSON/JSONL และรายการผลการวัดอื่นยังไม่หมุนหรือลบอัตโนมัติ จึงสะสมตามระยะเวลารัน แม้ใช้ `--duration 0` ให้เลือกช่วงเวลาที่ต้องการเก็บหลักฐานและหยุดเมื่อพอ ก่อนจัดเก็บหรือลบ raw artifacts ตามต้องการ

การเปิด workload ไม่เปิด ML เอง live forecast ยังคงปิดเป็นค่าเริ่มต้น หาก API ถูกเปิดด้วย `--experimental-synthetic-forecast` โมเดล synthetic จะทำงานตามข้อจำกัดเดิม การเห็นระบบสลับ WAN สำเร็จยังไม่พิสูจน์ว่า ML พยากรณ์ความแออัดล่วงหน้าได้หรือดีกว่า reactive policy

ข้อมูลรอบนี้ใช้สร้างชุดฝึก/ทดสอบจาก packet workloads ได้ในขั้นถัดไป ต้องแยกรอบการเก็บข้อมูลและเปรียบเทียบกับ baseline บนข้อมูลที่ไม่ใช้ฝึกก่อนอ้างประสิทธิภาพ ML โหมดนี้ยังไม่จำลองธรรมชาติของ ISP ครบถ้วน เพราะ WAN ทั้งสามใช้ทรัพยากรแล็บร่วมกัน
