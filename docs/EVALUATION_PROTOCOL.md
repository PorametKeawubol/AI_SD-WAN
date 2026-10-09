# Evaluation protocol: three WAN links

## ขอบเขตและคำถาม

ใช้ **balanced suite v2** เป็นค่าเริ่มต้น ครอบคลุมสถานะ Up/Down ทั้ง 8 แบบของ 3 WAN และเพิ่มการทดสอบคุณภาพลิงก์/ความเสถียร/ข้อมูล telemetry ตามประเภทปัญหา จำนวน cases เป็นขอบเขตการทดลองที่กำหนด ไม่ใช่จำนวนปัญหาทั้งหมดที่ 3 links จะเกิดได้

1. ระบบผ่านเกณฑ์ของแต่ละกลุ่มและแต่ละ WAN ได้กี่กรณี?
2. เมื่อยังมีเส้นทางที่ผ่าน SLA ระบบรักษาบริการและตอบสนองได้เร็วพอหรือไม่?
3. AI เพิ่มประโยชน์เหนือ priority/reactive/linear trend หรือไม่?
4. Forecast ของแต่ละ metric ดีกว่า persistence หรือไม่?

XGBoost ทำนาย metric ล่วงหน้า 30 วินาที แล้ว policy เลือก WAN ตาม SLA **ไม่ได้จำแนกเครือข่ายเป็น 16 หรือ 38 classes** แยก synthetic evaluation ออกจาก packet experiments จริงทุกครั้ง

## เหตุผลและจำนวน cases

| กลุ่ม | การออกแบบ | จำนวน |
|---|---|---:|
| Availability | Up/Down ครบ 2³ แบบ รวม CONTROL ที่ทุกลิงก์ up | 8 |
| Quality | latency, jitter, loss, saturation, gradual degradation × 3 WAN | 15 |
| Stability | flapping × 3 WAN | 3 |
| Mixed | latency บน WAN หนึ่ง + loss อีก WAN หนึ่ง; คู่ลิงก์แบบมีลำดับ 3×2 | 6 |
| Telemetry | missing/stale และ corrupt × 3 WAN | 6 |
| **รวม** | **37 fault cases + 1 normal CONTROL** | **38** |

เฉพาะ Up/Down เป็นการแจกแจงครบทุกสถานะ ส่วน quality/stability/mixed/telemetry เป็นชุดที่เลือกทดสอบที่ severity กำหนด ยังมีระดับความรุนแรง ลำดับเหตุการณ์และรูปแบบอื่นเพิ่มได้ การทดสอบ mixed เลือกคู่ลิงก์ต่างกันและคงหนึ่ง WAN เป็น healthy alternative

| WAN1 | WAN2 | WAN3 | Case |
|---|---|---|---|
| Up | Up | Up | CONTROL |
| Down | Up | Up | S01 |
| Up | Down | Up | S02 |
| Up | Up | Down | S03 |
| Down | Down | Up | S04 |
| Down | Up | Down | S05 |
| Up | Down | Down | S06 |
| Down | Down | Down | S07: detect no_path; ไม่มีเส้นทางให้กู้ connectivity |

S01–S16 คง ID และนิยามเดิมสำหรับเทียบหลักฐาน v1 เพิ่ม suffix เฉพาะกรณีใหม่ เช่น `S08-wan2`, `S15-wan3`, `S14-wan3-wan1` (WAN3 latency + WAN1 loss) นิยามร่วมอยู่ใน `sdwan/scenarios.py`; simulator และ netem runner ใช้แหล่งเดียวกัน

Manifest บันทึก suite/version, cases ที่เลือก, affected links, group counts และ SHA-256 รายงานใช้จำนวน cases ที่เลือกเป็น denominator และบอกว่าเป็น subset หรือครบ suite การขาด profiles/repeats หรือ trial ที่ invalid ไม่ได้รับ coverage credit

## Topology, initial priority และ SLA

| WAN | Capacity สมมติ | Cost | Synthetic baseline latency/jitter/loss/utilization |
|---|---:|---:|---|
| wan1 | 100 Mbps | 1.0 | 24 ms / 4 ms / 0.10% / 30% |
| wan2 | 80 Mbps | 1.2 | 40 ms / 7 ms / 0.15% / 40% |
| wan3 | 30 Mbps | 3.0 | 65 ms / 10 ms / 0.20% / 35% |

คง initial priority WAN1 ตาม protocol เดิมเพื่อให้เปรียบเทียบ strategies ภายใต้ baseline เดียวกัน การเสียของ WAN2/WAN3 ขณะที่ WAN1 ยังดีจึงทดสอบ standby handling และการไม่เปลี่ยนเส้นทางโดยไม่จำเป็นด้วย การเพิ่ม target coverage ยังไม่ใช่หลักฐาน active-path failover ครบทั้งสาม WAN; ต้องออกแบบ initial-priority rotation เป็น experiment แยกหากต้องการข้อสรุปนั้น Native QBR อาจเลือก fallback ก่อนเกิด fault จึงใช้ PCAP/UDP เพื่อประเมินเส้นทางจริง

| Application | Latency | Jitter | Loss | Utilization | Demand |
|---|---:|---:|---:|---:|---:|
| voice | ≤100 ms | ≤30 ms | ≤1% | ≤90% | 2 Mbps |
| interactive | ≤150 ms | ≤40 ms | ≤2% | ≤90% | 5 Mbps |
| bulk | ≤300 ms | ≤100 ms | ≤3% | ≤90% | 10 Mbps |

SLA เป็น lab configuration ใน `sdwan/domain.py` เกณฑ์เดิมถูกคงไว้ การเพิ่ม cases ไม่ลดเกณฑ์เพื่อให้ผ่าน

## Synthetic replay และข้อมูล ML

แต่ละ seed สุ่ม baseline ±15%, correlated noise, incident timing และ severity ทุก strategy replay trace เดียวกัน ไม่ให้ scenario ID เป็น feature Raw trace ของ 16 fault cases เดิมและ CONTROL ถูกตรวจ SHA-256 ว่าไม่เปลี่ยนที่ seed80000

Frozen model: training 60 episodes seeds1000–1059 และ validation15 episodes seeds1060–1074 แยกทั้ง episode; history/labels ไม่ข้าม boundary Target คือ metric(t+30s)−metric(t) Features ใช้ข้อมูลถึง t เท่านั้น XGBoost ราย metric ใช้เมื่อ validation MAE ดีกว่า persistence ≥2%; otherwise ใช้ persistence

`reports/latest` เป็น legacy v1 ที่ execute แล้ว: 16 faults + CONTROL ×30 seeds60000–60029 ×3 profiles ×5 strategies =7,650 controller trials เก็บผลและ model weights ไว้ครบ การปรับ catalogue รอบนี้ไม่ได้ retrain หรือ promote model

Balanced v2 default evaluation seed block70000; development smoke รอบนี้ใช้ seed80000 และ1 repetition รวม **38×3×5 =570 synthetic controller trials** อยู่ใน `reports/synthetic-balanced-smoke-20261002` ถือเป็น smoke สำหรับตรวจ pipeline ไม่ใช้เป็น final research acceptance หรือหลักฐาน real-data ML

หากทำ balanced synthetic30 repeats จะมี38×3×5×30=17,100 trials ต้องตรึง source/model/acceptance ก่อนรัน และใช้ output/seed block ใหม่หากปรับ policy หลังเห็นผล

## Strategies และเกณฑ์

Synthetic: priority, reactive SLA, linear trend, predictive without stability guards, predictive with guards ทั้งหมดใช้ freshness/quarantine เดียวกัน Reactive/AI ใช้ guardrails เดียวกันเพื่อแยกประโยชน์ forecasting

ต่อ synthetic trial:

- SLA attainment ≥98% ในช่วงที่มีเส้นทางผ่าน SLA อย่างน้อยหนึ่งเส้น
- Response ต่อเส้นทางเสีย SLA/down/stale ≤15 วินาที
- หลัง grace10วินาที ไม่เลือกข้อมูล down/stale และไม่แจ้ง no_path เมื่อยังมี path up/fresh
- All-down ต้องตรวจ no_path ภายใน polling interval5วินาที; นับ correct handling และไม่อ้าง connectivity restoration
- Normal CONTROL เปลี่ยนเส้นทางไม่เกิน2ครั้ง

ต่อ case อย่างน้อย95% ของ seeds ต้องผ่านทั้ง3 profiles รายงานแยก5กลุ่มและแยก CONTROL จาก fault denominator ในกรณี standby ที่ SLAไม่เคยเสีย ให้ตีความว่า **รักษา SLA ได้** และไม่อ้างว่าเกิด recovery/failover

Metrics: SLA, availability, response/recovery, switch count, false no_path, unsafe selections, forecast MAE เทียบ persistence และ paired differences Bootstrap/Wilson ใช้ seed เป็นหน่วย ไม่ถือ profiles/windows เป็น samples อิสระ One-repeat smoke ไม่พอประเมิน reliability

Synthetic capacity/headroom เป็น proxy ไม่ใช่ measured throughput Policy CPU timingไม่รวม inference/HTTP/controller/packet convergence

## Live packet protocol และการแบ่งรัน

ใช้ actual flexiEdges/netem/UDP/PCAP กับ4 strategies: native priority, native QBR, reactive และ experimental synthetic forecast ใช้ classifier/service class เดียวกันต่อ application Native QBR ให้ service observe และไม่เขียน policy

แต่ละ trial170วินาที: baseline70 → fault80 → restoration20 เกณฑ์เดิม: baseline SLA≥98%, generator offered≥90%demand; post-grace SLA≥98%, received≥95%offered, 10 good intervals ต่อเนื่องเริ่มภายใน15วินาที All-down expected delivery=0 และ no_path≤5วินาที Saturation ต้องเห็น target WAN utilization≥95% มิฉะนั้น invalid ใช้ load98% ของ capacity ของ WAN เป้าหมาย

แนะนำแบ่งการทดลองตามกลุ่มและเริ่ม voice/reactive/1 repeat ก่อน:

| ขอบเขต live plan | Trials | เวลาวัดขั้นต่ำ ไม่รวม startup/cleanup |
|---|---:|---:|
| Availability8 ×voice ×reactive ×1 | 8 | 22.7 นาที |
| Balanced38 ×voice ×reactive ×1 | 38 | 107.7 นาที |
| Balanced38 ×3profiles ×4strategies ×3 | 1,368 | 64.6 ชั่วโมง |

Grid v1 เดิม16×3×4×3=576 trials/27.2ชั่วโมงยังเก็บเป็นขอบเขต legacy ผล packet เดิมยังคงมี S07/S12 ที่ไม่ผ่าน การเพิ่ม cases ไม่ถือว่าแก้ failures หรือได้ผล packet ชุดใหม่แล้ว

TimescaleDB ใช้ stream ใหม่ต่อ trial พร้อม audit.json/database-stream.json บันทึก manifest/source/model hashes และ PCAP ห้ามใช้ output เดิมหรือข้าม unresolved journal Helpers ใช้ SSH host verification และตรวจ lab identity Live ML ยังคงปิดจน validate ข้อมูลจริง

## คำสั่งตรวจและทำซ้ำ

```powershell
# แสดง catalogue และกลุ่ม; JSON รวม manifest ที่ตรึงได้
.\.venv\Scripts\python.exe -m sdwan scenarios --suite balanced
.\.venv\Scripts\python.exe -m sdwan scenarios --suite balanced --json
# Simulation smoke ใหม่เท่านั้น; output ต้องใหม่
.\.venv\Scripts\python.exe -m sdwan eval --suite balanced --repetitions 1 --seed-base 81000 --output reports/synthetic-balanced-new
# ดู live plan โดยไม่เปลี่ยน network
.\.venv\Scripts\python.exe -m lab.run_live_eval --suite balanced --profiles voice --strategies reactive --repetitions 1 --plan
# เลือก availability ผ่าน manifest แทนการคัดลอก IDs
$catalogue = .\.venv\Scripts\python.exe -m sdwan scenarios --json | ConvertFrom-Json
$cases = $catalogue.cases | Where-Object group -eq availability | ForEach-Object id
# รัน packet จริงเมื่อพร้อม: หยุด decision API ก่อน ใช้ output ใหม่
.\scripts\run-wsl-experiment.ps1 -Suite balanced -OutputName live-availability-new -Scenarios $cases -Profiles voice -Strategies reactive -Repetitions 1
```

ใช้ `--suite legacy` เพื่อเลือก catalogue v1; live default legacyเลือก16 faults ส่วน synthetic legacyรวมCONTROLด้วย หลีกเลี่ยงการรวม v1/v2 หรือ synthetic/live เป็น pass rate เดียวกัน

รายละเอียดระบบจริงและข้อจำกัด measurement: [REAL_TESTBED.md](REAL_TESTBED.md) ผลเก่า: [CONTINUATION](../reports/flexiwan/CONTINUATION.md) นิยามใหม่: [balanced manifest](../reports/flexiwan/scenario-catalogue-balanced-20261002.json)
