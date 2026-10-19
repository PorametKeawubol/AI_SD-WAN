# ผลทดลอง real traffic 11 ตุลาคม 2026

รอบ `real-traffic-20261011-054158-1767b9` ใน stream `live-real-traffic-20261011-053846` รันการวัดเป้าหมาย 210 วินาที เริ่ม 05:42:05 UTC ใช้ Auto / Voice และโมเดล synthetic แบบ experimental รายละเอียดเชิงตัวเลขอยู่ใน [safe summary JSON](real-traffic-20261011-summary.json)

ใช้ HTTP request/download และ UDP echo ผ่าน flexiWAN LAN tunnel พร้อม iperf3 background load 6 streams แยก WAN/ฝั่ง A/B คงคอขวด 100/80/30 Mbps โดยไม่เติม delay/jitter/loss ระหว่างรัน ผลนี้เป็นแพ็กเก็ตจริงใน WAN เสมือน ไม่ใช่การทดสอบ ISP จริง

## สิ่งที่เกิดขึ้นจริง

- โหลด WAN1 ที่ขอ 110 Mbps เริ่ม batch แรกที่ **+68.722 วินาที** แม้ phase ตั้งไว้เริ่ม +60 วินาที เพราะรอ batch ก่อนจบ
- ระบบสร้าง apply intent **WAN1 → WAN2 ที่ +72.301 วินาที** และยืนยัน jobs/configuration ครบ 2 agents ที่ **+79.480 วินาที** หรือ 10.758 วินาทีหลังเปิดโหลดสูง API snapshot เห็น priority WAN2 ที่ +82.875 วินาที
- ตอนเสนอเปลี่ยน WAN1 วัด RTT ประมาณ 200.47 ms และ loss 6% เหตุผลคือ `Current SLA violation; healthy alternative` และขณะนั้นไม่มี forecast ใช้งานได้
- batch โหลดต่ำของ WAN1 เริ่มกลับมาที่ **+161.811 วินาที** ระบบเสนอ **WAN2 → WAN1 ที่ +196.645 วินาที** และยืนยันที่ **+202.279 วินาที** รอบกลับเกิดจาก WAN2 มี RTT standard deviation ประมาณ 32.21 ms เกิน Voice SLA 30 ms ไม่ใช่เพียงเพราะ WAN1 ฟื้น

API เคยแสดงคำเสนอ WAN3 ขณะ WAN2 กำลัง apply แต่ไม่มี verified apply ไป WAN3 รอบนี้มีการเปลี่ยนที่ยืนยันแล้วเพียงสองครั้ง

ก่อน verified apply แรก ในช่วงโหลดสูง UDP echo มี mean RTT **110.20 ms** และ packet-weighted loss **5.03%** หลัง verified apply ขณะโหลดสูงยังดำเนินต่อ mean RTT เป็น **4.19 ms** และ loss **0%** HTTP completion latency เฉลี่ยในสองช่วงนั้นเป็น **427.98 → 7.60 ms** ช่วงก่อนมีเพียง 11 UDP intervals และ 10 HTTP requests ส่วนหลังมี 82 ตัวอย่างต่อ workload จึงเป็นการสังเกตคนละช่วงเวลา ไม่ใช่การทดลองควบคุมที่พิสูจน์ผลของ AI

## ผลที่ปลายทางตาม phase

| Phase ตามเวลาเป้าหมาย | HTTP สำเร็จ | HTTP completion เฉลี่ย | Download เฉลี่ย | UDP echo RTT เฉลี่ย | UDP packet-weighted loss |
|---|---:|---:|---:|---:|---:|
| baseline 0–60 s | 60/60 | 11.36 ms | 70.72 Mbps (4 ครั้ง) | 5.16 ms | 0% |
| wan1_busy 60–150 s | 89/89 | 54.55 ms | 56.51 Mbps (6 ครั้ง) | 17.33 ms | 0.6101% |
| recovery 150–210 s | 59/59 | 6.47 ms | 51.02 Mbps (4 ครั้ง) | 2.61 ms | 0% |

การดาวน์โหลดทั้ง 14 ครั้งรับครบครั้งละ 4 MiB แต่ goodput ไม่กลับไปเท่าค่า baseline ใน phase recovery ซึ่งส่วนใหญ่ยังใช้ priority WAN2 ไม่ควรสรุปว่าทุก metric ดีขึ้นหลังสลับ

HTTP 208 requests, download 14 ครั้ง และ UDP 209 intervals รวม **431 แถว** ตรงกับข้อมูลใน TimescaleDB ทุกแถว ไม่มี HTTP/download ที่ไม่สำเร็จ แต่ UDP มีช่วง loss และ RTT สูง การมี echo กลับอย่างน้อยหนึ่งแพ็กเก็ตที่ทำให้ `success=true` จึงไม่ได้หมายความว่า SLA ผ่านทุก interval

Background receiver มีผลสมบูรณ์ **108 แถว** ครบทั้ง 6 bindings โหลดสูงของ WAN1 ส่งได้ใกล้ 110 Mbps แต่ receiver รับได้น้อยกว่าและรายงาน loss จริง อีก 6 raw files ของ batch สุดท้ายถูกหยุดก่อนจบ จึงไม่รวมเป็นผล receiver ที่สมบูรณ์

## หลักฐานและข้อจำกัด

PCAP พบ outer VXLAN UDP4789 และ inner IPv4 ESP(50) ทุกแพ็กเก็ตที่ตรวจ รวม WAN1 59,486 packets, WAN2 94,197 และ WAN3 1,443 สนับสนุนว่ามีทราฟฟิก tunnel เข้ารหัสจริง แต่การเข้ารหัสทำให้หลักฐานนี้ไม่ระบุว่าแพ็กเก็ตของแต่ละ application ย้ายเส้นตรงเวลาใด และยังรวมทราฟฟิกอื่นใน tunnel

runner จบรอบโดย `completed=true`, `active=false`, `restored_baseline=true` และไม่มี run/cleanup error journal ยืนยันการคืน classification ผ่าน jobs/readback 2 agents พร้อมคง priority WAN1 ที่ยืนยันล่าสุด ก่อนจบ API snapshot ไม่มี apply ค้าง natural WAN ไม่เริ่มใหม่เอง

รอบเดิมมี batch 110 Mbps สองฝั่งที่เริ่มใกล้ +150.037 วินาทีแล้วถูกติด phase label ว่า recovery รายงานนี้ใช้ **อัตราที่ขอจริง** แยกช่วงโหลด และโค้ดติด label ถูกแก้หลังรอบนี้ Raw JSON/JSONL และ PCAP อยู่ใน ignored `artifacts/real-traffic/<run_id>/`

นี่เป็นรอบเดียวบนทรัพยากรแล็บร่วมกัน ไม่มีผลเปรียบเทียบ reactive/no-controller ภายใต้โหลดเดียวกันและยังไม่ได้ validate ML ด้วยข้อมูลจริง การสลับแรกไม่มี forecast ส่วนรอบกลับมี forecast แต่ trigger เป็น SLA ปัจจุบัน ผลนี้ยืนยัน pipeline วัด–ตัดสินใจ–สั่งงาน–ยืนยันผลและผล endpoint ที่สังเกตได้ โดยไม่อ้างว่าโมเดลพยากรณ์ความแออัดล่วงหน้าหรือดีกว่า baseline

ก่อนรอบนี้มี smoke/debug runs ที่ต้องแก้ receiver/startup และการเขียน capture รอบที่หยุดก่อนครบไม่ได้รวมในสถิติข้างต้นหรือถูกนับเป็น successful demo รายงานนี้ระบุ run ID ของรอบที่จบครบเพื่อแยกหลักฐานอย่างชัดเจน

รอบเริ่มซ้ำหลังแก้ `real-traffic-20261011-055036-dfcea6` จบครบ 210 วินาที ได้ endpoint 432 แถว การตรวจหลังหยุดยืนยัน classifier/agents และ qdisc `CONTROL` ทั้ง 6 จุด ไม่มี process/namespace ของ workload ค้างและ unresolved intents เป็น 0 รอบนี้คง priority WAN2 ที่ยืนยันล่าสุดไว้ ผลตรวจแยกอยู่ใน [restart verification](real-traffic-20261011-restart-verification.json) และไม่รวมกับสถิติรอบแรกข้างต้น
