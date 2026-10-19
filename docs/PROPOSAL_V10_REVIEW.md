# เทียบ proposal เวอร์ชัน 10 ตุลาคมกับระบบปัจจุบัน

ตรวจวันที่ 11 ตุลาคม 2569 โดยเทียบ `E:/Proposal/coop-proposal-Poramet-v10-10-2026.docx` กับ source code, รายงานผลทดลอง และภาพที่ฝังในเอกสาร ไม่ได้แก้ DOCX ต้นฉบับ

**สถาปัตยกรรมและขอบเขตหลักตรงกัน:** flexiWAN, FastAPI/Python, XGBoost, TimescaleDB/PostgreSQL, Grafana, Auto/Manual, Approve/Reject และ isolated testbed มีในระบบแล้ว จุดที่ควรแก้คือรายละเอียดโมเดล จำนวนและชนิด WAN วิธีทดสอบ นิยามค่าที่วัด และภาพ flow ส่วนข้อความที่เป็นเป้าหมายหรือ “คาดว่าจะ” ยังเก็บไว้ได้

เอกสารนี้ระบุตำแหน่งด้วยหัวข้อ ตาราง ภาพ และข้อความเริ่มต้น เพื่อใช้ค้นหาใน Word ไม่ใช้เลขหน้าจากสารบัญที่อาจเปลี่ยนเมื่อแก้เอกสาร การตรวจครอบคลุมเนื้อหาและภาพฝัง ไม่ใช่การรับรอง layout หรือ pagination ของ DOCX

## รายการที่ควรแก้ก่อน

| จุดใน proposal | สถานะปัจจุบัน | แนวทางแก้ |
|---|---|---|
| 9.4, 10.3, ภาพ 8 | ใช้ XGBoost regression แยกสี่ metrics ทำนายอีก 30 วินาที | ระบุชนิดโมเดลและผลลัพธ์ให้แน่นอน แยกการคำนวณคะแนน/เลือก WAN ของ policy |
| 9.5, ตาราง 3 | 26 features จากประวัติ 12 samples บน grid 5 วินาที | เพิ่ม lag ที่ใช้จริง, history fraction และแก้ std เป็นส่วนเบี่ยงเบนมาตรฐาน |
| 10.3, ภาพ 8 | pipeline ใหม่แบ่ง train/tune/acceptance/holdout เป็นทั้ง episode | เพิ่มการป้องกัน leakage, persistence baseline, gate และการเก็บ candidate แยกจาก live |
| 7.1, 10.1–10.2, ตาราง 1/4, ภาพ 7 | สอง edges กับสาม WAN เสมือน 100/80/30 Mbps | ปรับภาพสอง WAN และป้าย ISP ให้ตรง testbed; แยกขอบเขตขั้นต่ำจากสิ่งที่ติดตั้งจริง |
| 7.2, 9.3, ตาราง 3/5 | probe ใช้ RTT/RTT stddev และ VPP counters | ระบุนิยามและข้อจำกัดของ metrics; แยก endpoint goodput จาก capacity proxy |
| 7.4, 9.8, ตาราง 1/2 | controller ผู้ผลิตมีจริง; central GUI ที่ทดลองเป็น community read-only | แยก backend, GUI และ Grafana controls; เพิ่ม MongoDB/Redis ในบทบาท controller |
| 9.6–9.7, 10.4, ภาพ 9 | ใช้ current SLA เป็นหลัก มี guards, approval expiry และ verified apply | เพิ่มทางไม่มี forecast, ข้อยกเว้น cooldown, revalidation และ jobs/agent readback |
| 7.5, 10.5, ตาราง 4/5 | มี simulation และ packet trials; native QBR ทดสอบแล้วบางส่วน | ระบุชื่อ baseline กับ coverage ของแต่ละชุด และเกณฑ์พิสูจน์ประโยชน์ ML |

## 1 ระบุโมเดลที่พัฒนาจริง

ตำแหน่ง: **9.4** ย่อหน้าที่เริ่ม “ในโครงงานนี้ ข้อมูล Network Telemetry” และรายการ Regression/Classification; **10.3** ข้อฝึกและประเมินโมเดล

เอกสารยังเปิดทางให้เลือก Regression หรือ Classification แต่ระบบปัจจุบันฝึก residual regression แยก latency, jitter, loss และ utilization ไม่ได้มี classifier Normal/Degraded/Unavailable และไม่ได้ฝึกให้โมเดลตอบว่าเลือก WAN ใด คะแนนคุณภาพและคำแนะนำเกิดจาก Path Decision policy

ข้อความแนะนำสำหรับ 9.4:

> โครงงานนี้ใช้ XGBoost ในรูปแบบ Regression เพื่อคาดการณ์ค่า Latency, Jitter, Packet Loss และ Bandwidth Utilization ล่วงหน้า 30 วินาที โดยฝึกโมเดลแยกสำหรับแต่ละตัวแปรให้ทำนายผลต่างระหว่างค่าในอนาคตกับค่าปัจจุบัน ผลการคาดการณ์ใช้เป็นข้อมูลประกอบ Path Decision ร่วมกับข้อมูลที่ตรวจวัดปัจจุบันและเกณฑ์ SLA ส่วนการตรวจสถานะ Up/Down การคำนวณคะแนนคุณภาพ และการเลือก WAN Path ดำเนินการในกลไก Path Decision

ข้อความแนะนำแทนรายการประเมินใน 10.3:

> ประเมินความคลาดเคลื่อนด้วย Mean Absolute Error (MAE) แยกแต่ละตัวแปร และเปรียบเทียบกับ Persistence ซึ่งใช้ค่าที่วัดล่าสุดเป็นค่าคาดการณ์ พร้อมประเมินประโยชน์ต่อ SLA และการเลือกเส้นทางแยกจากความคลาดเคลื่อนของโมเดล

หากยังต้องการศึกษา Classification ในอนาคต ให้ระบุเป็นงานเพิ่มเติม ไม่ใส่ Accuracy/Precision/Recall/F1 เป็นผลของ regression ที่ทำอยู่

หลักฐาน: [model.py](../sdwan/model.py), [policy.py](../sdwan/policy.py), [ผลปรับ ML](ML_OPTIMIZATION.md)

## 2 เติมรายละเอียด features และแบ่งข้อมูล

ตำแหน่ง: **9.5**, **10.3**, **ตาราง 3**, **ภาพ 8**

รายการ lag/rolling/trend สอดคล้องแล้ว แต่ควรระบุรายละเอียดและแก้แถว Rolling Standard Deviation จาก “ความแปรปรวน” เป็น “ส่วนเบี่ยงเบนมาตรฐาน”

ข้อความแนะนำ:

> สร้าง Features จากประวัติ 12 samples บน grid 5 วินาที โดยแต่ละ metric ประกอบด้วยค่าปัจจุบัน ค่าย้อนหลัง 5 และ 15 วินาที ค่าเฉลี่ย ส่วนเบี่ยงเบนมาตรฐาน และอัตราการเปลี่ยนแปลงในหน้าต่างย้อนหลัง พร้อมสถานะ Up และสัดส่วนความครบของประวัติ รวม 26 features หากข้อมูลไม่ครบ ไม่ต่อเนื่อง หรือมี Down samples ที่ไม่เข้าเงื่อนไข live sampling ระบบจะไม่เติมข้อมูลที่ขาดเพื่อให้ได้ forecast และจะใช้ข้อมูลปัจจุบันในการตัดสินใจ

ข้อความแนะนำแทนรายการ “แบ่งข้อมูลออกเป็นชุดสำหรับการฝึกโมเดลและการประเมินผล”:

> สำหรับการพัฒนาโมเดลรุ่นปรับปรุง แบ่งข้อมูลเป็น Train, Tune, Acceptance และ Holdout ตามลำดับเวลาของทั้ง episode โดยข้อมูลทุก WAN ใน episode เดียวกันอยู่ในชุดเดียวกัน และไม่ให้หน้าต่างประวัติหรือ label ล่วงหน้าข้ามขอบเขต episode ใช้ Tune สำหรับเลือกโมเดล ใช้ Acceptance ตรวจ candidate ที่เลือกแล้ว และใช้ Holdout รายงานผลหลังตรึงการเลือก เพื่อป้องกันข้อมูลรั่วไหล

เพิ่มหลังรายการเลือกโมเดล:

> ฝึกและคัดเลือกโมเดลแบบ Offline โดยเปรียบเทียบกับ Persistence แยกแต่ละ metric Candidate ต้องลด MAE บน Acceptance อย่างน้อย 2% มีอย่างน้อยสาม episode groups และ 30 windows ต่อ metric พร้อม episode-block bootstrap 95% confidence interval ของผลต่าง MAE ที่อยู่ต่ำกว่า 0 ทั้งช่วง Metric ที่ไม่ผ่านเกณฑ์จะใช้ Persistence และจะไม่เลือก candidate อื่นจากผล Acceptance ที่เห็นแล้ว การเปลี่ยนโมเดล live ต้องตรวจผลแยกก่อนนำเข้าใช้งาน

เกณฑ์เหล่านี้เป็นเกณฑ์ที่กำหนดในแล็บ จำนวน groups อย่างเดียวไม่ได้ยืนยันว่าข้อมูลเป็นอิสระ การเก็บข้อมูลรอบถัดไปต้องหลีกเลี่ยง replay ที่เหมือนกันและกำหนด partitions ล่วงหน้า โมเดล live เดิมมาจาก pipeline train/validation รุ่นก่อน จึงไม่เขียนว่าโมเดลเดิมผ่าน acceptance ใหม่แล้ว

หลักฐาน: [features.py](../sdwan/features.py), [optimization.py](../sdwan/optimization.py), [ผลปรับ ML](ML_OPTIMIZATION.md)

## 3 ทำ topology และ workload ให้ตรงแล็บ

ตำแหน่ง: **7.1**, **10.1** ข้อ WAN อย่างน้อย 2 เส้นทาง, **10.2**, **ตาราง 1**, **ตาราง 4**, **ภาพ 7**

“อย่างน้อย 2 เส้นทาง” ไม่ผิดในฐานะขอบเขตขั้นต่ำ แต่ภาพ 7 แสดงเพียง WAN1 (ISP1) / WAN2 (ISP2) ต่างจากแล็บที่ใช้งานจริง และป้าย ISP อาจทำให้เข้าใจว่าเป็นวงจรผู้ให้บริการจริง

ข้อความแนะนำสำหรับ testbed:

> สภาพแวดล้อมทดสอบประกอบด้วย flexiEdge สอง Site เชื่อมผ่าน WAN เสมือนสามเส้น โดยกำหนดความจุ WAN1 100 Mbps, WAN2 80 Mbps และ WAN3 30 Mbps บนโฮสต์แล็บเดียวกัน ใช้ Linux namespaces, bridges และ traffic control เพื่อควบคุมสภาพ WAN การทดลองนี้จำกัดอยู่ใน testbed และยังไม่ได้ใช้วงจรอินเทอร์เน็ตจริงจากผู้ให้บริการสามราย

เพิ่มวิธีทดลองใน **7.5 / 10.5**:

> ทดสอบทั้งการจำลองความผิดปกติด้วย NetEm และการรับส่งทราฟฟิกจริงใน testbed โดยสร้าง HTTP request, download และ UDP application traffic ผ่าน LAN tunnels ร่วมกับ iperf3 background traffic ที่ตรึงแยกแต่ละ WAN แล้ววัด response time, goodput, loss และ jitter ตามนิยามของแต่ละเครื่องมือที่ปลายทาง ระหว่าง real-traffic workload ใช้การจำกัดความจุและคิว โดยไม่เติม delay/jitter/loss จาก natural WAN profile

ระบบเลือกหนึ่ง application profile ในแต่ละครั้ง ไม่ได้ optimize เส้นทางแยกสำหรับ HTTP/download/UDP พร้อมกัน แม้จะส่ง workload เหล่านี้ร่วมกันได้

แก้ตาราง 4 แถว Normal จาก “WAN Path ทั้งสองเส้น” เป็น “WAN Path ทั้งสามเส้น” และเพิ่มวิธีทดสอบ High Utilization/Congestion ด้วย offered load และ receiver measurements โดยไม่ถือว่า VPP utilization ต้องเพิ่มตาม background เสมอ

หลักฐาน: [REAL_TESTBED](REAL_TESTBED.md), [REAL_TRAFFIC](REAL_TRAFFIC.md), [domain.py](../sdwan/domain.py), [linux_topology.py](../lab/linux_topology.py)

## 4 ระบุนิยามค่าที่วัด

ตำแหน่ง: **7.2**, **9.3**, **10.2** รายการรวบรวมข้อมูล, **ตาราง 3 / 5**

นิยามในเอกสารเป็นนิยามทั่วไป ควรเพิ่มนิยามเฉพาะการทดลองเพื่อให้ตีความกราฟและ SLA ถูกต้อง:

| ค่า | นิยามที่ตรงกับระบบ |
|---|---|
| Probe latency | ICMP RTT mean ของแต่ละ WAN ไม่ใช่ one-way delay |
| Probe jitter | RTT standard deviation; ไม่รวมกับนิยาม receiver jitter ของ iperf3 |
| Probe packet loss | เปอร์เซ็นต์การสูญเสียจากหน้าต่าง ICMP 100 packets มีความละเอียด 1 จุดเปอร์เซ็นต์ |
| Edge utilization | อัตราจาก VPP RX/TX counters โดยใช้ทิศทางที่สูงกว่า เทียบความจุ WAN ที่กำหนด |
| Background offered/received load | ค่าที่ส่งและรับของ iperf3 แยกแต่ละ WAN; โหลดนี้ไม่ผ่าน VPP จึงไม่รวมใน edge utilization |
| Endpoint performance | HTTP response time, download goodput และ UDP echo results รายงานแยกตามวิธีวัด |
| Simulation capacity/headroom | proxy ที่คำนวณจากความจุและ utilization ไม่ใช่ throughput/goodput ที่ปลายทางวัดได้ |

ข้อความแนะนำสำหรับการเก็บข้อมูล:

> Collector เรียก Probe ที่กำหนดไว้บน flexiEdge ผ่าน authenticated flexiManage REST/RPC ประมาณทุก 5 วินาที โดยส่ง ICMP ไป remote underlay ของแต่ละ WAN และอ่าน VPP interface counters ข้อมูล underlay ใช้ประกอบการตัดสินใจ และตรวจผล application/overlay แยกที่ปลายทางเพื่อยืนยันคุณภาพการรับส่งจริง

หลักฐาน: [probe.py](../lab/edge/probe.py), [collector.py](../sdwan/collector.py), [domain.py](../sdwan/domain.py), [นิยาม workload](REAL_TRAFFIC.md)

## 5 แยก controller GUI และฐานข้อมูลแต่ละส่วน

ตำแหน่ง: **7.1 / 7.4**, **9.8**, **10.2**, **ตาราง 1 / 2**

FastAPI/Python/XGBoost/TimescaleDB/Grafana ในตาราง 1 ตรงแล้ว ควรเติมบทบาทของแต่ละระบบ:

> ใช้ flexiManage 6.3.37 แบบ self-host เป็น SD-WAN Controller และ flexiEdge 6.3.35 สองเครื่อง ส่วน GUI ส่วนกลางที่ทดลองในแล็บเป็น Community frontend แบบอ่านอย่างเดียว การแสดงผล AI/ML การเปลี่ยน Auto/Manual และ Approve/Reject ใช้ Grafana พร้อม Control and approvals panel ที่เชื่อมกับ Telemetry & Decision Service ผ่าน authenticated HTTPS proxy

| องค์ประกอบ | สิ่งที่ใช้จริงและบทบาท |
|---|---|
| Virtualization | Windows + WSL2 Ubuntu 24.04, KVM/libvirt/QEMU; edge guests Ubuntu 20.04 |
| Controller | flexiManage ใน Docker พร้อม MongoDB/Redis เป็น dependencies ภายใน controller |
| Telemetry/decision storage | PostgreSQL 17 + TimescaleDB 2.30.2; Grafana metadata ใช้ PostgreSQL ด้วย |
| Monitoring/control | Grafana 13.2.3 กับ panel/plugin สำหรับ control API |
| Traffic/evidence | NetEm/tc, iperf3, HTTP/UDP workloads และ tcpdump |
| Lab bootstrap mail | Mailpit สำหรับรับอีเมลภายในแล็บ |

แถว REST API ในตาราง 1 อาจระบุเฉพาะ “HTTPS พร้อม TLS verification และ JSON” ให้ตรงการติดตั้งนี้

ตาราง 2 เป็นทรัพยากรที่เสนอได้ แต่ยังไม่มีผลพิสูจน์ minimum resources ของ service VM 4 vCPU/8 GB/50 GB ระบบปัจจุบันใช้ services ร่วมบน WSL host และแต่ละ edge VM ใช้ **2 vCPU / 4 GB RAM / qcow2 virtual disk 20 GB** ควรแยก “ทรัพยากรที่เสนอ” กับ “ทรัพยากรที่ติดตั้งจริง” ไม่เปลี่ยนคำว่า minimum ให้เป็นผล benchmark

หลักฐาน: [COMMUNITY_FLEXIMANAGE_GUI](COMMUNITY_FLEXIMANAGE_GUI.md), [REAL_TESTBED](REAL_TESTBED.md), [compose.yaml](../lab/compose.yaml), [linux_topology.py](../lab/linux_topology.py)

## 6 อธิบาย Path Decision และการยืนยันผล

ตำแหน่ง: **7.3**, **9.6 / 9.7**, **10.4**, **ภาพ 9**

ข้อความ current condition เป็นหลักและ prediction เป็นข้อมูลเพิ่มเติมตรงกับโค้ดแล้ว ควรเพิ่มขอบเขตและเงื่อนไข:

> ระบบที่พัฒนาขึ้นปรับลำดับ Priority ของ WAN Path Labels ผ่าน REST API ของ flexiManage โดยควบคุมหนึ่ง dedicated policy และหนึ่ง application profile ที่เลือกในแต่ละครั้ง ใช้ค่าที่วัดปัจจุบันและ SLA เป็นหลัก ผล forecast ที่ใช้งานได้เพิ่มคะแนนความเสี่ยงเมื่อคาดว่าคุณภาพจะเสื่อมลง โดยไม่ใช้คำทำนายลบล้างการผิด SLA ปัจจุบัน หากไม่มี forecast ระบบยังตัดสินใจจากข้อมูลปัจจุบันได้

> การสลับตามคุณภาพปกติใช้ Hysteresis 0.15, การยืนยันข้อมูลใหม่สามครั้งห่างกันอย่างน้อย 5 วินาที และ Cooldown 30 วินาที ส่วนเส้นทางปัจจุบันที่ Down/ข้อมูลหมดอายุ หรือผิด SLA ขณะที่มีเส้นทางอื่นผ่าน SLA สามารถข้ามเงื่อนไขรอเหล่านี้ได้ ค่าดังกล่าวเป็น configuration ของแล็บที่ต้องประเมินผล มิใช่ค่าที่พิสูจน์แล้วว่า optimal

> ใน Manual Mode คำแนะนำมีอายุ 30 วินาที ก่อน Apply ระบบตรวจ ID อายุคำแนะนำ source path และสภาพเครือข่ายปัจจุบันอีกครั้ง หากเงื่อนไขไม่ผ่านจะไม่ Apply คำแนะนำเดิม ส่วน Reject ยกเลิกคำแนะนำนั้นโดยไม่ส่งคำสั่งเปลี่ยน policy

> หลังส่งคำสั่ง ระบบตรวจ Controller jobs และอ่านกลับ Policy จาก Agents ทั้งสองก่อนบันทึก Verified Apply และเปลี่ยนค่าลำดับ Priority ที่ยืนยัน หากผลไม่แน่นอนจะไม่ถือว่า Apply สำเร็จ และใช้ Apply journal สำหรับ reconciliation

เพิ่มข้อความการอ่าน Dashboard:

> Configured priority แสดงค่าที่ตรวจจาก Agents ส่วนการยืนยันว่าแพ็กเก็ตส่งผ่าน WAN ใดต้องตรวจ packet capture ประกอบ endpoint results เพราะ native failover อาจใช้เส้นทางสำรองได้

ทฤษฎี 9.2 ที่กล่าวถึง Priority/Load Balancing/QBR เก็บไว้ได้ แต่ไม่ระบุว่าระบบ AI ปัจจุบัน implement ทั้งสามแบบ การเปลี่ยน policy ของ AI ใช้ Priority ส่วน native QBR ใช้เป็น baseline แยก

หลักฐาน: [policy.py](../sdwan/policy.py), [service.py](../sdwan/service.py), [adapters.py](../sdwan/adapters.py), [Control plugin](../lab/grafana/control-plugin/README.md)

## 7 ทำ baseline และเกณฑ์ประเมินให้ตรวจสอบได้

ตำแหน่ง: **7.5**, **10.2** ข้อ baseline, **10.5**, **ตาราง 4 / 5**

มี native Priority/QBR จริงใน packet runner และผล S01 voice **12 trials = สี่ strategies × สาม repeats** แล้ว จึงไม่ควรเขียนว่า native baseline ยังไม่มี แต่ simulation จำนวนมากใช้ Python priority/reactive/linear-trend/predictive policies ไม่ใช่ native QBR ทุกกรณี Full packet grid ยังไม่ได้รันครบ

ข้อความแนะนำ:

> เปรียบเทียบ Predictive policy กับ Reactive policy ที่ใช้ค่าปัจจุบัน และ baseline แบบ Priority/Quality-Based Routing ของ flexiWAN ภายใต้เงื่อนไขและ workload เดียวกัน โดยรายงานผล Simulation และผลรับส่งแพ็กเก็ตจริงใน testbed แยกกัน พร้อมระบุจำนวน cases, repetitions, invalid trials และ coverage ของแต่ละชุด

> ประเมินผลคาดการณ์ด้วย MAE เทียบ Persistence และประเมินประโยชน์ต่อระบบด้วย SLA violations, packet loss, goodput, เวลาฟื้นตัวของ application และจำนวนการเปลี่ยนเส้นทางจาก trials ที่เปรียบเทียบกันได้ การ failover สำเร็จหรือการเปลี่ยนก่อน SLA violation เพียงอย่างเดียวไม่ถือเป็นหลักฐานว่า ML เพิ่มประสิทธิภาพ

ปรับตาราง 5 ให้แยกเวลาที่มีความหมายต่างกัน:

| ตัวชี้วัด | ข้อความแนะนำ |
|---|---|
| Failover / traffic recovery time | เวลาจากเหตุการณ์ผิดปกติจนทราฟฟิกปลายทางฟื้นตามเกณฑ์และความละเอียดการวัดที่กำหนด |
| Decision processing time | เวลาประมวลผล Path Decision; แยกจากเวลาส่งคำสั่งและยืนยันบนอุปกรณ์ |
| Verified policy apply time | เวลาจากส่งคำสั่งจน jobs และ agent readback ผ่าน |
| Recommendation-to-Apply Time | เวลาจากสร้าง recommendation จน verified apply สำเร็จ |
| Approval Response Time | เวลาจากสร้าง recommendation จนได้รับ Approve/Reject |
| Throughput/goodput | อัตราข้อมูลที่ receiver/endpoint รับได้จริง แยกจาก offered load และ simulation capacity proxy |
| Prediction Error | MAE แยกสี่ metrics ณ t+30 วินาที พร้อมผล Persistence และ sampling/eligibility contract |

Recommendation-to-Apply และ Approval Response มี instrumentation แล้ว ไม่ใช่งานที่ยังขาด แต่ simulation policy CPU time ไม่แทนเวลาจนแพ็กเก็ตเปลี่ยนเส้นทางจริง

หลักฐาน: [baselines.py](../lab/baselines.py), [evaluation.py](../sdwan/evaluation.py), [packet trial coverage](REAL_TESTBED.md), [ผลปรับ ML](ML_OPTIMIZATION.md)

## 8 ภาพที่ควรปรับ

ตรวจภาพฝัง 6–9 โดยตรงแล้ว:

| ภาพ | สิ่งที่ควรปรับ |
|---|---|
| 6 ภาพรวมสถาปัตยกรรม | หากสื่อ testbed ปัจจุบันให้ใช้สอง edges; ภาพเดิมแสดงสาขา สำนักงานใหญ่ และ cloud edge ให้ติดป้ายว่าเป็นแนวคิดหากจะเก็บไว้ เปลี่ยน ML output เป็น forecast metrics และให้ Path Decision ใน service เป็นผู้สร้าง recommendation การ inference live ใช้ประวัติใน service; DB ใช้เก็บ/โหลดข้อมูล ไม่ได้ query DB เพื่อฝึกใหม่ทุกครั้ง |
| 7 Multi-WAN Testbed | เพิ่ม WAN3 เปลี่ยน ISP1/ISP2 เป็น WAN เสมือนสามเส้น 100/80/30 Mbps เพิ่ม background receivers ที่ตรึงกับ WAN และ application endpoints ถ้าต้องการแสดงโหมด workload ใหม่ |
| 8 ML Development Pipeline | เปลี่ยน Train/Validation/Test เป็น Train/Tune/Acceptance/Holdout เพิ่ม Persistence/gate และ candidate ที่แยกจาก live ใช้ regression MAE แทนชุด classification metrics และให้ output เป็น forecast metrics ไม่ใช่ learned Path Quality Score |
| 9 Auto/Manual flow | เชื่อมกล่อง Hold Time/Cooldown เข้ากับทางเดินจริง กล่องในภาพเดิมลอยจาก Yes branch เพิ่มกรณี down/stale ที่ข้าม cooldown ทางไม่มี forecast และ revalidation หลัง Approve เพิ่ม jobs/agent readback กับทาง apply failed/uncertain ก่อนยืนยันผล |

ภาพ SD-WAN และ flexiWAN ที่ใช้ประกอบทฤษฎีไม่ต้องเปลี่ยนให้เหมือนแล็บทุกจุด แต่ captions ควรแยก “ตัวอย่าง/แนวคิด” ออกจาก “ระบบที่ติดตั้งจริง”

มี [flow ของระบบปัจจุบัน](../artifacts/ai-sdwan-system-flow.png) และ [source Mermaid](../artifacts/ai-sdwan-system-flow.mmd) ใช้อ้างอิงการปรับภาพได้ แต่ควรตรวจว่า flow ฉบับที่จะใส่มี workload และ candidate pipeline ที่เพิ่มวันที่ 11 ตุลาคมครบก่อนใช้แทนภาพเดิม

## 9 เป้าหมายที่ควรเก็บและสถานะที่ควรเพิ่ม

ไม่ต้องลบชื่อโครงการ AI-Driven SD-WAN, วัตถุประสงค์การคาดการณ์ หรือข้อความ “คาดว่าจะช่วย” ในหัวข้อ 5/8 เพราะเป็น hypothesis/เป้าหมายของ proposal และเอกสารฉบับนี้ไม่ได้อ้างตัวเลข accuracy หรือว่า ML ชนะ baseline แล้ว ควรเขียนผลสรุปตามหลักฐานเมื่อทดสอบเสร็จ แม้ผลออกมาเท่ากับหรือแย่กว่า baseline

ถ้าต้องการเพิ่มสถานะการพัฒนาปัจจุบัน ให้แยกย่อหน้าจากแผน เช่นท้าย 10.3:

> ณ วันที่ 11 ตุลาคม 2569 ระบบรับส่งข้อมูล ควบคุม Priority และรองรับ Auto/Manual ผ่าน Grafana ได้ใน testbed โมเดลที่เปิดใช้ในแล็บฝึกจากข้อมูลจำลองและอยู่ในสถานะทดลอง โมเดลรุ่นปรับปรุงถูกเก็บเป็น candidate แยกจาก live ผลข้อมูลจริงยังไม่ผ่านเกณฑ์ยืนยัน และผลเปรียบเทียบล่าสุดยังไม่พบข้อได้เปรียบด้าน SLA เหนือ Reactive จึงยังไม่สรุปว่า Machine Learning เพิ่มประสิทธิภาพการเลือกเส้นทางจริง

ค่า cache speedup เป็นผลด้านการอ่าน forecast ซ้ำ ความแม่นยำไม่ได้เพิ่มจาก cache และ tests ที่ผ่านยืนยันพฤติกรรมโค้ดในเงื่อนไขที่ทดสอบ ไม่ใช่หลักฐานว่า ML ชนะ baseline

หลักฐานสถานะ: [ผลปรับ ML](ML_OPTIMIZATION.md), [runtime verification](../reports/flexiwan/ml-optimization-20261011-runtime.json)

## จุดบรรณาธิการที่พบเพิ่มเติม

ตารางเอกสารอ้างอิงมีข้อความ `%1`, `%11` และรูปแบบชื่อผู้แต่งที่รวม initials อ่านยาก ควรตรวจ Bibliography fields/style ใน Word และปรับรายการอ้างอิงให้ครบก่อนส่งฉบับสุดท้าย การตรวจนี้ไม่ได้ตรวจความถูกต้องของทุกบทความหรือทุก URL

เมื่อแก้จำนวน WAN, captions, ตาราง หรือรายละเอียดหัวข้อแล้ว ให้อัปเดตสารบัญ สารบัญรูป และสารบัญตารางใน Word พร้อมตรวจ page breaks อีกครั้ง วันที่ลงนาม 20 กรกฎาคม 2569 ในหน้าใบรับรองควรตรวจตามวันลงนามจริง ไม่เปลี่ยนเป็นวันที่แก้ไฟล์โดยอัตโนมัติ

## บันทึกแหล่งตรวจ

DOCX ต้นฉบับ SHA-256: `3bdede40b70b0e640309ca1d487dc023fffec87b08fecfe71e058c8d1b976956`

อ่าน body paragraphs/tables ตามลำดับ รวม 429 body blocks, 9 tables และ 10 embedded images ไม่พบ tracked insertions/deletions ใช้ source และรายงานที่ลิงก์ในแต่ละข้อ ไม่ได้ถือข้อความใน proposal เป็นคำสั่งให้เปลี่ยนระบบ
