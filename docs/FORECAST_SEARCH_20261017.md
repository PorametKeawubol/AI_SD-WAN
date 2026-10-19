# การปรับปรุงและประเมินการทำนายคุณภาพลิงก์

โมเดลเปรียบเทียบเดิมที่ใช้จริงใน Linux เก็บสำเนาตรงไฟล์ไว้ที่ `reports/forecast-confirmation-20261017/original-linux-comparator` ส่วนไฟล์เดิมฝั่ง Windows มี `loss_pct.json` ที่ไม่ถูกเปิดใช้ต่างกัน จึงมี fingerprint ต่างกัน แต่ตรวจค่าทำนายจากทั้งสองชุดบนทุกหน้าต่างทดสอบสุดท้ายแล้วตรงกันทั้งหมด ไม่ได้แทนที่ไฟล์เดิมฝั่งใด ดู `model-copy-verification.json` สำหรับรหัสตรวจสอบ

## การยืนยันเพิ่มเติมด้วยข้อมูลใหม่หลังปรับโมเดล

ใช้ 12 episodes เดิมทั้งหมดเป็น Train รวมถึง Holdout เดิมที่เคยเปิดดูแล้ว และเก็บใหม่อีก 9 episodes ด้วย seed ที่ประกาศก่อนเริ่ม แบ่ง Tune/Acceptance/Holdout เป็น 3/3/3 รวมชุดข้อมูล 21 episodes ไม่ได้ใช้ Holdout เดิมซ้ำเป็นหลักฐานยืนยัน โมเดลยังไม่ถูกนำขึ้น runtime

ค้นหา Ridge 5 ค่า alpha และ XGBoost 4 ชุดพารามิเตอร์ด้วย MAE objective เลือกน้ำหนัก residual จาก 0/.25/.5/.75/1 ด้วย Tune เท่านั้น ตรวจ Acceptance ครั้งเดียวต่อตัวชี้วัด ค่าที่ไม่ผ่านกลับไป Persistence โดยไม่เลือกตัวอื่น และรายงาน Holdout ทั้งหมดหลังตรึงผล

| ตัวชี้วัด | Persistence | Linear trend | โมเดลเดิม | รุ่นปรับปรุงก่อนหน้า | รุ่นปรับปรุงใหม่ | ลด MAE จาก Persistence |
|---|---:|---:|---:|---:|---:|---:|
| RTT (ms) | 47.512554 | 74.332199 | 60.686928 | 11.540712 | 10.310433 | 78.30% |
| Jitter (ms) | 2.515329 | 3.680517 | 3.610996 | 0.637780 | 0.632354 | 74.86% |
| Loss (จุดเปอร์เซ็นต์) | 0.072464 | 0.111989 | 0.072464 | 0.072464 | 0.000381 | 99.47% |
| Utilization (จุดเปอร์เซ็นต์) | 21.515371 | 31.270855 | 25.574516 | 5.901011 | 5.114695 | 76.23% |

แต่ละตัวชี้วัดมี 69 หน้าต่างที่ใช้ประเมินจาก Holdout ใหม่ทั้งสามรอบ เปรียบเทียบทุกวิธีบนหน้าต่างเดียวกัน ไม่ตัดรอบตามผล และไม่ปรับโมเดลตาม Holdout

ผลเลือก Ridge สำหรับ RTT และ loss และ XGBoost สำหรับ jitter และ utilization ทั้งสี่ตัวผ่าน Acceptance อย่างไรก็ตาม เป้าหมาย loss ใน Holdout ทั้ง 69 ค่าเป็น 0% จึงยังไม่ยืนยันความสามารถคาดการณ์ช่วง packet loss ค่า MAE ของ loss ที่ต่ำมากไม่ใช้เป็นข้อสรุปความเหนือกว่าทั่วไป และ CI แบบรายรอบของ loss คร่อมศูนย์ บันทึกข้อจำกัดโดยไม่เปลี่ยน gate หรือเลือกโมเดลใหม่หลังเปิด Holdout

| ตัวชี้วัด | พารามิเตอร์ที่เลือก | ผ่าน Acceptance | ผลต่าง MAE ใหม่ลบ Persistence: CI 95% บน Holdout |
|---|---|---|---|
| latency_ms | `{"family": "ridge", "alpha": 0.1, "tuning_weight": 1.0, "candidate_weight": 1.0, "tuning_mae": 8.44298783916896}` | True | `[-44.3390766539009, -31.377421124952132]` |
| jitter_ms | `{"family": "xgboost", "depth": 2, "lambda": 20, "min_child_weight": 10, "best_iteration": 233, "tuning_weight": 1.0, "candidate_weight": 1.0, "tuning_mae": 0.3500464856624603}` | True | `[-2.338241924842199, -1.5970269498370944]` |
| loss_pct | `{"family": "ridge", "alpha": 0.1, "tuning_weight": 1.0, "candidate_weight": 1.0, "tuning_mae": 0.0005535848295789741}` | True | `[-0.1243846155884514, 0.0004979089260470195]` |
| utilization_pct | `{"family": "xgboost", "depth": 3, "lambda": 5, "min_child_weight": 1, "best_iteration": 239, "tuning_weight": 1.0, "candidate_weight": 1.0, "tuning_mae": 2.398592948913574}` | True | `[-18.818910598754883, -14.318356786455427]` |

ข้อมูลใหม่ 675 measured WAN frames ส่ง ICMP 67,500 packets และตรวจย้อน RTT/jitter/loss จาก stdout ครบทุก batch ตรงกัน ส่วน utilization ตรวจย้อนจาก TX counters ดิบ ก่อน/หลัง ระยะเวลาวัด และอัตราสูงสุดครบ 675 แถว ต่างจากรอบก่อนที่ไม่มี counters ดิบ เก็บ PCAP ตัวอย่าง 1,000 packets ต่อ WAN เท่านั้น

ภาพอธิบายโมเดลใช้ TreeSHAP จากโมเดลที่บันทึกไว้ ตรวจว่าผลรวม contribution รวม bias ตรงกับ residual prediction กราฟ trace ใช้ WAN1 ของ Holdout รอบแรกที่กำหนดไว้ ไม่เลือกรอบที่สวยที่สุด และจัดเวลาเทียบค่าคาดการณ์ที่ t กับค่าจริงที่ t+30 ความสำคัญของ feature เป็นคำอธิบายการทำนาย ไม่ใช่หลักฐานเหตุและผล

ผลยังจำกัดใน ICMP/UDP ผ่าน Linux veth/NetEm บนเครื่องเดียวและรูปแบบสภาพจาก generator ตระกูลเดียว ไม่มี flexiEdge/VPP/overlay อยู่ในเส้นทาง และไม่มีผล SLA ใหม่ จึงต้องยืนยันผ่าน flexiEdge ก่อนนำไปเลือกเส้นทาง ขณะตรวจเพิ่ม /dev/kvm เปิดไม่ได้ (ENODEV) และ VM ทั้งสองยังปิด ไม่มีการถอนอัปเดตหรือรีสตาร์ตเพิ่มเติม

- `artifacts/link-campaign-confirmation-20261017/`: ข้อมูลและหลักฐานแพ็กเก็ตใหม่
- `artifacts/link-confirmation-report-20261017/`: ข้อมูลรวม protocol audit ทุก comparator และคำอธิบาย
- `artifacts/model-search-link-v2-20261017/`: โมเดลใหม่ metadata และผลการค้นหา
- `.codex-review/readability-ml-20261017/ml-protocol.json`: แผนที่เขียนก่อนเก็บข้อมูล (ฝั่ง Windows)

Model fingerprint ใหม่: `6397fe7394d23e3579e1ee2215dfd671097278ac890ee0b0e83f6bb1e01afc82`

Dataset canonical SHA256: `a13b40d455bd18d17a8d7479eddbe705707e65825dd8532d13a6a705148adc7c`

การตรวจเฉพาะส่วนที่ปรับ: pytest 44 passed และ Ruff ผ่าน collector กับ tests โมเดล runtime เดิมตรวจ fingerprint ก่อนและหลังแล้วยังตรงเดิม


ทดลองค้นหาโมเดล residual จาก XGBoost และ Ridge โดยเทียบกับการใช้ค่าล่าสุด (persistence) บนข้อมูลจำลองชุดใหม่ ข้อมูล flexiEdge เดิม และแพ็กเก็ตจริงใน Linux link testbed ใหม่ โมเดลเดิมใน artifacts/model ยังคงเดิมและไม่มีการ promote โมเดลรอบนี้ ผล forecast กับผล routing/SLA แยกประเมินกัน

## ผลแพ็กเก็ตระดับลิงก์ใหม่

เก็บครบ 12 episodes, 900 measured WAN frames, ICMP 90,000 packets offered และ UDP background load ที่ส่งจริง ผ่าน 6 private network namespaces/veth/NetEm อัตรา 100/80/30 Mbps ไม่มี flexiEdge, VPP หรือ overlay อยู่ในเส้นทาง และไม่ใช่ข้อมูลวงจร ISP จริง Utilization วัดจาก TX bytes ของ veth ไม่ใช่ VPP counters

ตรึง Train/Tune/Accept/Holdout 4/2/3/3 episodes ก่อนเริ่ม เก็บทั้งสาม WAN ของแต่ละรอบไว้กลุ่มเดียวกัน เงื่อนไขประกอบด้วย stable/ramp/step-busy/recovery เปลี่ยน seed, phase และ amplitude ระหว่าง episodes ไม่มีตารางฉีดปัญหาหรือ seed เป็น model feature

| Metric | Last value | Linear trend | Frozen model | Gated candidate | Gain vs last value |
|---|---:|---:|---:|---:|---:|
| RTT (ms) | 44.303551 | 73.241180 | 52.542397 | 8.595037 | 80.60% |
| Jitter (ms) | 2.172462 | 3.286880 | 2.899266 | 0.405272 | 81.35% |
| Loss (percentage points) | 0.125000 | 0.193182 | 0.125000 | 0.125000 | 0.00% |
| Utilization (percentage points) | 17.678396 | 27.523428 | 21.780056 | 3.075892 | 82.60% |

| Metric | Selected family | Tune weight | Acceptance gain | 95% episode-block CI | Accepted |
|---|---|---:|---:|---|---|
| RTT (ms) | xgboost | 1.0 | 69.42% | [-37.81284713745117, -21.53649139404297] | True |
| Jitter (ms) | xgboost | 1.0 | 70.66% | [-2.0676467418670654, -1.2234630584716797] | True |
| Loss (percentage points) | ridge | 1.0 | 98.40% | [-0.19044550591762788, 0.003785654541511734] | False |
| Utilization (percentage points) | xgboost | 1.0 | 76.46% | [-22.32733154296875, -13.777463912963867] | True |

Acceptance ต้องลด MAE >=2%, อย่างน้อย 30 windows และ 3 episode groups ต่อ metric และขอบบนของ paired episode-block bootstrap95%CI ต่ำกว่า0 หากไม่ผ่านใช้ persistence เท่านั้น ไม่ลอง candidate อื่นหลังดู acceptance Holdout รายงานหลัง freeze selection/gate แล้ว ไม่มีการปรับตามผลนั้น

## การทดสอบความทั่วไป

ข้อมูลจำลอง seed block310000 มี115 episodes แบ่ง60/15/30/10; RTT/jitter/utilization ผ่าน gate ส่วน loss ไม่ผ่านเกณฑ์2% (acceptance gainประมาณ1.73%) จึงใช้ persistence ผล holdout เทียบค่าล่าสุด:

| Metric | Persistence | Gated candidate | Gain |
|---|---:|---:|---:|
| RTT (ms) | 18.359163 | 15.472352 | 15.72% |
| Jitter (ms) | 7.100034 | 5.442339 | 23.35% |
| Loss (percentage points) | 2.596516 | 2.596516 | 0.00% |
| Utilization (percentage points) | 3.531530 | 2.770746 | 21.54% |

Balanced suite แยกจากการฝึก ใช้seed91000,38cases×3profiles×5strategies×3repetitions=1710trials/model ผลข้ามชุดยังไม่ชนะ persistence สม่ำเสมอ:

| Metric | Persistence | Frozen | Candidate |
|---|---:|---:|---:|
| RTT (ms) | 17.598078 | 21.569314 | 20.423863 |
| Jitter (ms) | 3.275373 | 3.962220 | 3.643316 |
| Loss (percentage points) | 1.119925 | 1.119925 | 1.119925 |
| Utilization (percentage points) | 2.724760 | 2.384408 | 2.362163 |

Predictive และ Reactive จัดการ37/37fault cases และ36/36recoverable cases เท่ากัน Paired SLA difference=0,bootstrap95%CI=[0,0] จึงไม่มีหลักฐาน SLA advantage ในชุดนี้ Comparator Priority ในsimulationเป็นpolicyจำลอง ไม่ใช่ native flexiWAN

ข้อมูล flexiEdge เดิม8episodesแบ่ง3/1/1/3 และถูกตรวจดูแล้ว เป็น exploratory เท่านั้น Acceptanceมี1groupจึงไม่ผ่านทุกmetric ผลholdout486windowsไม่ใช้ยืนยันการpromote ข้อบกพร่อง packetเดิมS07no_path10.00s และS12SLA97.14%ยังไม่ถือว่าแก้แล้ว

## สภาพแวดล้อมและข้อจำกัด

เครื่อง Ryzen7 7735HS,32GB,Windows11 build26300.9711; WSL3.0.3.0/kernel6.18.54.1,Python3.12.3,XGBoost3.4.1,scikit-learn1.9.1 โมดูลkvm_amdโหลดไม่ได้: Operation not supported / SVM not supported by CPU ทั้งสองVMจึงยังปิด ไม่มีการ rollback Windows update เปลี่ยนค่าความปลอดภัยหรือ reboot

อาการสอดคล้องกับ [ปัญหา nested virtualization ที่ Microsoft รับทราบ](https://github.com/microsoft/WSL/issues/41917) แต่บันทึกท้องถิ่นเพียงอย่างเดียวยังไม่ยืนยันสาเหตุของเครื่องนี้ การแก้ที่รวมในPR41942เพิ่มคำเตือนและไม่ได้แก้Windowsregressionต้นเหตุ

การทดลองใหม่มีเพียง3acceptanceและ3holdoutgroupsบนเครื่องเดียว รูปแบบสภาวะแม้ต่างseedยังมาจากgeneratorชนิดเดียว ไม่ครอบคลุมall-down,flapping,telemetryfault,ISPหรือVPPdomain จึงห้ามใช้เป็นหลักฐาน routing/SLA superiority หรือเปลี่ยนโมเดลlive

ภาพ GUI ที่ใช้ในข้อเสนอคัดจากบันทึก10October2026เดิม เฟรม140และ350วินาที ไม่ใช่ภาพการทดลองวันนี้ Computer Use ถูก automatic review ระงับเมื่อตรวจURLของหน้าต่างเบราว์เซอร์ที่ซ้อนอยู่ไม่ได้ จึงไม่มีภาพVSCodeใหม่ ไม่สร้างภาพหน้าจอทดแทนขึ้นเอง

## หลักฐานและวิธีทำซ้ำ

- artifacts/link-campaign-20261017/: protocol,dataset,status,rawprobe stdout,conditions และ PCAP ส่วนหัว (sample1000frames/WAN ไม่ใช่การจับICMP90,000packetsทั้งหมด)
- artifacts/model-search-{synthetic,real-exploratory,link}-20261017/: models,metadata,report/search.json
- reports/forecast-search-20261017/summary.json: ผลรวมพร้อม episode IDs,counters,CI,fingerprints และข้อจำกัด
- reports/search-balanced-{candidate,frozen}-20261017/: JSON,CSV,plots รายstrategy/case
- reports/flexiwan/forecast-environment-20261017.json: เวอร์ชันและKVMdiagnostics
- .work/forecast-protocol-20261017.json: collection/searchplanที่เขียนก่อนเริ่มเก็บแพ็กเก็ต

```bash
.venv/bin/python -m lab.link_forecast_campaign --plan
.venv/bin/python -m sdwan.forecast_search --plan
.venv/bin/python -m sdwan.forecast_search \
  --dataset artifacts/link-campaign-20261017/dataset.json \
  --counts 4 2 3 3 --baseline-model artifacts/model \
  --output artifacts/model-search-link-new \
  --provenance isolated_linux_veth_netem
```

ทุกoutputต้องใหม่ ใช้โค้ดForecasterที่รองรับoptional linear_heads เพื่อโหลดRidge JSON ไม่มีdependencyใหม่ beyond scikit-learnเดิม การทดสอบซ้ำด้วยdatasetเดิมเป็นreproduction ไม่ใช่ fresh confirmation

## การตรวจโค้ดและขั้นต่อไป

`.venv/bin/python -m pytest -q`:352passed,7skipped,1dependencywarning,21.86s; Ruffผ่านไฟล์ที่แก้ Model loaderรักษาพฤติกรรมbundleเดิม ตรวจlinearhead/scalerและsavedroundtrip แยกTrain/Tune/Accept/Holdout และทดสอบcleanupของnamespaceที่สร้างสำเร็จเท่านั้น

เก็บข้อมูลใหม่ผ่านflexiEdge/VPP/overlayต่างวันหลังKVMพร้อม และทำpairedapplicationtrialsกับnativepriority,QBR,Reactive,predictive กำหนดmetricsรองก่อนรัน: วินาทีที่อยู่บนเส้นทางเสียSLAตั้งแต่faultเริ่มโดยไม่ตัดgrace,warningleadtime,unnecessaryswitches พร้อมคงSLAหลังgraceเป็นprimarycriterionเดิม

Frozen model fingerprint ยังคง b7b72bfa7c1f34be948b397c4aaebdc639093657c15b444992d8bef3b2bbfc7d

รอบนี้เก็บค่า utilization ที่คำนวณจาก TX bytes แล้ว แต่ไม่ได้เก็บ TX counter ดิบราย sample จึงตรวจย้อนคำนวณส่วนนี้ไม่ได้ ต้องเพิ่ม raw counter logging ในการเก็บข้อมูลครั้งถัดไป ผล RTT/jitter/loss ของทุก 900 batches ตรวจคำนวณกลับจาก stdout ของ ICMP ได้ตรงทั้งหมด ดู reports/flexiwan/forecast-packets-20261017.json
