# ผลปรับปรุง ML — 11 ตุลาคม 2026

นำการลดคำนวณ forecast ซ้ำเข้า live แล้ว และสร้าง pipeline ฝึก/คัดเลือกโมเดลใหม่พร้อมผลทดลองแยกไว้ **ยังไม่ได้เปลี่ยนโมเดล live** เพราะ candidate ไม่ได้ชนะทุกการทดสอบ และข้อมูลจริงยังไม่เพียงพอสำหรับการยืนยันประสิทธิภาพ

หลัง restart ตรวจว่า API กลับเป็น **Auto / voice / priority WAN1**, มี probe สดครบสาม WAN, agents ทั้งสองอ่าน policy ตรงกัน และ unresolved intents เป็น 0 โมเดลเดิมยังเปิดด้วย flag experimental ตามการตั้งค่าก่อนหน้า ดู [ผลตรวจ runtime](../reports/flexiwan/ml-optimization-20261011-runtime.json)

## ส่วนที่ปรับและใช้งานแล้ว

`sdwan/service.py` เก็บผล forecast ของข้อมูลชุดล่าสุดไว้ เมื่ออ่านซ้ำหรือ watchdog ตรวจซ้ำโดยไม่มี telemetry ใหม่ จะใช้ผลเดิม การรับ telemetry ใหม่ การเปลี่ยน sampling contract หรือการเปลี่ยน model object ทำให้คำนวณใหม่ ผลที่ส่งให้ผู้เรียกเป็นสำเนาเพื่อป้องกันการแก้ cache จากภายนอก และข้อผิดพลาดไม่ได้ถูก cache

วัดบน WSL ด้วย frozen model เดิม, ประวัติสาม WAN และ storage ที่ไม่เรียก SQL/controller:

| การอ่าน forecast | เวลามัธยฐานต่อครั้ง |
|---|---:|
| คำนวณซ้ำทุกครั้ง | 2.625 ms |
| ใช้ cache เมื่อข้อมูลไม่เปลี่ยน | 0.00561 ms |
| มี telemetry ใหม่และต้องคำนวณ | 2.907 ms |

การอ่านข้อมูลชุดเดิมเร็วขึ้นประมาณ **468 เท่า** และคำทำนายตรงกัน การวัดนี้ครอบคลุมเฉพาะ forecast read ไม่ใช่ความเร็ว API ทั้งระบบหรือความแม่นยำ ML ดู [timings, versions และ source hashes](../reports/flexiwan/ml-optimization-20261011-cache-benchmark.json)

`sdwan/model.py` รองรับน้ำหนักผสม residual ของ XGBoost กับ persistence แยกแต่ละ metric โดยสูตรคือ `current + weight × predicted_delta` น้ำหนัก 0 ใช้ค่าล่าสุด และ 1 ใช้ residual เต็ม โมเดลเก่าที่ไม่มีน้ำหนักยังให้ผลเดิม ตัว loader ตรวจ schema, น้ำหนัก และ finite feature matrix ก่อนทำนาย

## Pipeline ฝึกและคัดเลือกใหม่

ใช้ `sdwan/optimization.py` สำหรับทดลอง offline แบ่ง **ทั้ง episode** ตามลำดับเวลาเป็น train → tune → acceptance → holdout และเก็บ WAN ทั้งสามของ episode ไว้ใน partition เดียวกัน History และ target ไม่ข้าม episode หรือ WAN

1. สร้าง feature 26 ค่า จากประวัติ 12 samples บน grid 5 วินาที เพื่อทำนาย metric ที่อีก 30 วินาที
2. ใช้เฉพาะ history ที่เข้าเงื่อนไขเดียวกับ live; ข้ามช่องว่างที่ resample ไม่ได้ และไม่นำ sentinel latency/jitter ตอน WAN down มาเป็นค่าคุณภาพปลายทาง ส่วน loss/utilization ที่ยังวัดได้ประเมินตาม mask ของตัวเอง
3. ฝึก XGBoost residual แยกสี่ metrics โดยลอง depth 3/5 และเลือกน้ำหนัก 0, 0.25, 0.5, 0.75, 1 จาก **tune เท่านั้น** Early stopping ใช้ tune ด้วย
4. ตรวจ candidate ที่เลือกแล้วกับ acceptance หนึ่งครั้ง ต้องลด MAE เทียบ persistence อย่างน้อย 2%, มีอย่างน้อยสาม episode groups และ 30 windows ต่อ metric พร้อม episode-block bootstrap 95% CI ของผลต่าง MAE ที่อยู่ต่ำกว่า 0 ทั้งช่วง
5. Metric ที่ไม่ผ่านกลับไปใช้น้ำหนัก 0 ไม่ลอง candidate อื่นจากผล acceptance และอ่าน holdout หลังตรึงการเลือกแล้วเพื่อรายงานเท่านั้น

เกณฑ์นี้ประเมินความคลาดเคลื่อน forecast ประโยชน์ต่อ SLA และการสลับ WAN ต้องทดสอบแยก การนับ episode groups ไม่รับประกันว่าข้อมูลอิสระจริง จึงต้องดูที่มาของข้อมูลด้วย

สร้าง bundle ใน staging เฉพาะการรัน ตรวจโหลดครบก่อนเผยแพร่ และปฏิเสธ output ที่มีอยู่แล้ว Pipeline ไม่เรียก controller หรือ promote โมเดล ส่วนคำสั่ง train เดิมก็ปฏิเสธ directory ที่มีไฟล์เพื่อรักษา frozen evidence

## ผลข้อมูลจำลองที่กันไว้

ใช้ seed block ใหม่เริ่ม 200000 จำนวน 115 episodes แบ่ง train 60 / tune 15 / acceptance 30 / holdout 10 สี่ metrics ผ่าน acceptance; น้ำหนักเป็น 1 ทั้งหมด ตารางนี้เป็น **MAE ของ 10 holdout episodes จาก generator เดิม** ค่าเล็กกว่าดีกว่า

| Metric | Candidate ใหม่ | Frozen เดิม | Persistence |
|---|---:|---:|---:|
| Latency (ms) | 15.845 | 16.605 | 21.878 |
| Jitter (ms) | 5.085 | 5.343 | 6.877 |
| Loss (percentage points) | 3.639 | 3.682 | 3.682 |
| Utilization (percentage points) | 3.418 | 3.452 | 4.302 |

Latency/jitter ลด MAE เทียบ persistence ประมาณ **27.6% / 26.1%** แต่ loss บน holdout ดีขึ้นเพียง 1.17% แม้จะผ่านเกณฑ์ 2% ใน acceptance ดู [protocol, partitions และผลทุก metric](../reports/flexiwan/ml-optimization-20261011-synthetic.json)

เมื่อนำไปตรวจ fault suite อีกชุดด้วย seeds 220000–220002 จำนวนสาม repetitions, 38 cases × 3 profiles × 5 strategies = **1,710 trials ต่อโมเดล** พบว่าผลไม่ชนะครบทุก metric:

| Metric | Candidate ใหม่ | Frozen เดิม | Persistence |
|---|---:|---:|---:|
| Latency (ms) | 19.909 | 21.527 | **17.449** |
| Jitter (ms) | 3.726 | 3.938 | **3.272** |
| Loss (percentage points) | 1.177 | **1.118** | **1.118** |
| Utilization (percentage points) | 2.511 | **2.498** | 2.713 |

AI และ reactive ผ่าน fault response 37/37 และ recovery 36/36 เท่ากันทั้งสองโมเดล ผลต่าง SLA แบบ paired ของ AI เทียบ reactive เป็น 0 จึงยังไม่มีหลักฐานว่า candidate ทำให้การ routing ดีกว่า reactive การประเมิน legacy suite นี้รวม history บางแบบที่ pipeline ใหม่ไม่ใช้ จึงอ่านเป็นอีก diagnostic แยกจากตาราง holdout ไม่รวมคะแนนเข้าด้วยกัน ดู [ผลเปรียบเทียบสองชุด](../reports/flexiwan/ml-optimization-20261011-balanced-comparison.json)

## ผลจาก probe และทราฟฟิกจริงในแล็บ

ส่งออก probe จาก TimescaleDB เป็นแปด completed episodes: natural WAN ห้ารอบและ real-traffic สามรอบ แบ่ง natural สามรอบแรก train, รอบสี่ tune, รอบห้า acceptance และ real-traffic สามรอบ holdout หลังตรวจ cadence/history/target เหลือ holdout **486 windows** จากสามรอบ (6 / 60 / 420)

ข้อมูลนี้เคยถูกตรวจมาก่อนแล้ว และ natural WAN ห้ารอบใช้ seed เดียวกัน จึงเป็น **post-hoc transfer diagnostic** ไม่ใช่ชุดยืนยันอิสระสำหรับ promotion WAN ยังเป็นเส้นเสมือน มีการจำกัดความจุ ไม่ใช่ ISP จริงสามราย ดู [audit และ manifest ของข้อมูล](../reports/flexiwan/ml-real-data-audit-20261011.json)

Acceptance มีเพียงหนึ่ง episode group จึงไม่ผ่านจำนวน groups และ CI ทุก metric Candidate สำหรับข้อมูลจริงจึงมีน้ำหนัก 0 ทั้งสี่ค่า ตารางนี้แสดง MAE บน real-traffic holdout:

| Metric | ML ที่ tune เลือก ก่อน gate | หลัง gate = Persistence | Frozen เดิม |
|---|---:|---:|---:|
| Latency (ms) | 21.919 | **21.180** | 30.914 |
| Jitter (ms) | 9.075 | **8.901** | 11.303 |
| Loss (percentage points) | **0.695** | 0.848 | 0.848 |
| Utilization (percentage points) | 2.014 | **0.789** | 3.360 |

Loss ของ ML ที่ tune เลือกดีกว่า persistence ใน diagnostic นี้ แต่ยังไม่ผ่าน acceptance ส่วน latency/jitter/utilization โอนไป real-traffic ได้แย่กว่ persistence จึงคงโมเดลทดลองแยกไว้ ดู [ผล candidate จากข้อมูลจริง](../reports/flexiwan/ml-optimization-20261011-real.json)

Latency ของ probe เป็น RTT; jitter เป็นความแกว่งของ RTT ตาม collector ไม่ใช่ one-way latency หรือ iperf receiver jitter ส่วน utilization มาจาก counters ของ edge และ **ไม่รวม competing background traffic ที่อยู่นอก VPP** ต้องอ่านคู่กับ receiver และ application measurements ตาม [นิยาม real traffic](REAL_TRAFFIC.md)

## รันซ้ำและตำแหน่งไฟล์

จาก root ของ repo ที่ติดตั้ง dependencies แล้ว:

```bash
python -m sdwan.optimization --plan
python -m sdwan.optimization --output artifacts/model-candidate-new --baseline-model artifacts/model
python scripts/benchmark_forecast_cache.py --output reports/flexiwan/cache-benchmark-new.json
```

ใช้ชื่อ output ใหม่ทุกครั้ง ตัวอย่างข้อมูลจริงที่รันใน workspace นี้:

```bash
python -m sdwan.optimization --dataset .work/real-probe-episodes.json \
  --train-episodes 3 --tune-episodes 1 --accept-episodes 1 --holdout-episodes 3 \
  --output artifacts/model-real-candidate-new --baseline-model artifacts/model
```

Dataset ต้องเป็น JSON schema 1 มี `data_kind` (`synthetic` หรือ `real_recorded_probe`), `sampling` (`strict` หรือ `causal_linear_5s`) และ `episodes` แต่ละ episode มี `id` กับ `series` ครบ `wan1`, `wan2`, `wan3` เป็นรายการ Telemetry ที่เรียง timestamp เพิ่มขึ้นไม่ซ้ำ Episode ต้องไม่ทับช่วงเวลากัน จำนวน episodes ต้องตรงผลรวม partitions

Raw dataset ใน `.work` และ weights ใน `artifacts/model-optimized-synthetic-20261011`, `artifacts/model-optimized-real-20261011` เก็บใน workspace และไม่เข้า Git รายงาน JSON ที่ลิงก์ไว้เข้า Git ได้ ไม่มี credentials โมเดล live ยังเป็น frozen เดิม fingerprint `b7b72bfa7c1f34be948b397c4aaebdc639093657c15b444992d8bef3b2bbfc7d`

ก่อนพิจารณาเปลี่ยนโมเดล live ต้องเก็บ fresh episodes ที่มีโหลด/ระยะเวลา/ลำดับเหตุการณ์หลากหลายและไม่ replay seed เดิม แยก tune, acceptance และ final evaluation ล่วงหน้า มีหลาย independent acceptance groups แล้วตรวจทั้ง forecast MAE และ paired application/SLA trials พร้อม verified policy และ packet evidence ผลรอบนี้สนับสนุนการใช้ cache และ pipeline ใหม่ แต่ยังไม่สนับสนุนการ promote candidate

สำหรับ candidate ที่ใช้ `strict` ต้องจัดให้ผู้เรียกกรอง down history ตาม training eligibility ด้วย ตัว `Forecaster.predict` เดิมตรวจ cadence แต่ไม่ได้กรอง down history; live causal sampler กรองอยู่แล้ว ข้อนี้ไม่กระทบ cache ที่นำเข้า live และต้องตรวจให้ตรงกันก่อนนำ strict candidate ไปใช้ในเส้นทางอื่น

## การตรวจหลังปรับ

ชุดทดสอบ model/cache/optimization/service/sampling/diagnostic/policy/evaluation ผ่าน **93 tests** และ Ruff ผ่าน Source ที่เกี่ยวข้องบน Windows กับ Linux ตรงกัน และ candidate ทั้งสองมี fingerprint ตรงกับรายงาน Frozen bundle บน Windows กับ Linux มี JSON formatting ของ loss model ต่างกัน แต่เนื้อหาที่ parse แล้วเหมือนกัน และ metric นี้ใช้ persistence อยู่แล้ว

Grafana อ่านครบ **25 queries โดยไม่มี query error** การ์ด control และ forecast มีข้อมูล หลัง workload จบ panels endpoint/background ว่างในช่วงล่าสุด 15 นาทีตามปกติ; ดูผลรอบที่รันได้โดยเลือกช่วงเวลาในอดีต ดู [ผลตรวจและ fingerprints](../reports/flexiwan/ml-optimization-20261011-validation.json)
