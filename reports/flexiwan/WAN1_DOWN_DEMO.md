# WAN1 down demonstration — 10 October 2026

ผู้ใช้ขอจำลอง down เพื่อดูการทำงานของ ML เปิดทดสอบกับ decision API เดิมใน **Auto / voice / experimental synthetic forecast** ไม่มี policy writer ตัวที่สองและไม่มีการ inject telemetry ปลอม หยุด natural environment ชั่วคราว ตรวจ topology/VM/qdisc baseline แล้วใช้ S01: NetEm loss 100% ทั้งสองทิศทางของ WAN1 โดย management และ WAN2/WAN3 ยังทำงาน

| เหตุการณ์ | ผลที่วัดได้ |
|---|---|
| เริ่มตัด WAN1 | 10 ต.ค. 2026 เวลา 02:50:38 น. กรุงเทพฯ |
| ตัดสินใจเลือก WAN2 | 5.37 วินาทีหลังตัด; `Current path down/stale; bypass cooldown` |
| UDP กลับมาดีต่อเนื่อง | เริ่มประมาณ 4.99 วินาทีหลังตัด วัดจาก 10 ช่วงต่อเนื่อง ช่วงละ 1 วินาที |
| Controller/agent ยืนยัน priority WAN2 | 20.68 วินาทีหลังตัด; verified readback ครบ 2 edges |
| คืน WAN1 | 02:52:09 น.; down รวม 90.33 วินาที |
| หลังเปิด natural environment กลับ | Auto คง WAN2 ชั่วคราวตาม hysteresis แล้วกลับ WAN1 ด้วย `Sustained quality improvement` |

UDP echo จริงจาก LAN client `10.20.0.2` ไป server `10.30.0.2:5209` เสนอโหลด 2 Mbps ต่อเนื่อง ก่อน fault รับได้ 100%; ช่วง down 90 วินาทีรับได้รวม 95.55% ซึ่งรวมช่วงขาดประมาณ 4 วินาทีแรก; ช่วง restoration รับได้ 99.97% การจับ ESP ใน VXLAN แสดง application-sized packets บน WAN1 ก่อนตัด แล้วบน WAN2 ระหว่าง down/หลังคืนเส้น WAN3 เป็น standby และ PCAP มีเฉพาะ header ไม่มี large application packets

UDP ฟื้นก่อนเวลาที่ Auto ยืนยันการเปลี่ยน policy และพบ packets บน WAN2 ก่อนเริ่ม apply ของ Auto ลำดับนี้สอดคล้องกับ native failover ของ flexiWAN จึงไม่ใช้เวลาฟื้นของ UDP เป็นเวลาที่ ML หรือ controller apply สำเร็จ PCAP ที่ namespace interface `b` อาจเห็น packets ก่อน qdisc drop และมี snaplen 160; การเห็น header อย่างเดียวไม่ใช่หลักฐานว่า packet ส่งถึงปลายทาง ใช้ผล UDP ประกอบ

## สิ่งที่ ML แสดงในรอบนี้

Model โหลดอยู่ตลอด แต่ **ไม่มี forecast ใช้ใน decision แรกที่สั่งเปลี่ยน WAN1 → WAN2** การสลับครั้งนี้เกิดจากกฎ down/stale ปัจจุบัน หลังช่วง cadence/history ขาดต่อเนื่อง ตัว inference ปฏิเสธหน้าต่างที่ไม่ครบหรือไม่เข้า sampling contract จึงมีช่วงไม่มี forecasts ประมาณหนึ่งนาที จากนั้น forecasts ของ WAN2/WAN3 กลับมาขณะ WAN1 ยัง down เมื่อคืน WAN1 และมีประวัติต่อเนื่องเพียงพอ forecasts กลับมาครบสามเส้น

เพิ่ม read-only Grafana panels **Latest decision and reason** และ **ML 30s forecasts and measured WAN quality** เพื่อดู action/reason, measured RTT/jitter/loss/utilization, forecast ล่วงหน้า 30 วินาที และ decision score ค่าว่างหมายถึงไม่มี forecast ที่ใช้ได้ ไม่แทนด้วยศูนย์ Forecast module ใช้ XGBoost เฉพาะ metrics ที่ผ่าน synthetic validation gate และใช้ persistence สำหรับ metrics ที่ปิดอยู่ ทั้งสองตารางแสดง state ล่าสุดของ stream ที่เลือก ส่วนกราฟและ audit ใช้ช่วงเวลาที่เลือก

ผลนี้เป็นการสาธิตครั้งเดียว ไม่ใช่ผลผ่าน repeated benchmark และยังไม่พิสูจน์ว่า ML ทำนายเหตุล่มฉับพลันล่วงหน้าได้หรือดีกว่า reactive ไม่มีการเปลี่ยน frozen model weights, SLA thresholds หรือ guardrails

ตรวจทั้ง 18 SQL panels ผ่าน Latest live และชื่อ stream โดยตรงก่อน/ระหว่าง/หลัง demo API และ agent readback รอบติดตามยืนยันว่า priority กลับ WAN1, Auto / voice / model ทำงาน, สาม WAN สดและ UP, forecasts ครบสามเส้น, natural environment เปิดอยู่ และไม่มี pending/inflight/unresolved intent ไม่พบ control-loop, watchdog หรือ apply error ในช่วง demo ไม่มี interactive browser QA; คำสั่งเปิดเบราว์เซอร์อัตโนมัติถูก policy ของเครื่องมือปฏิเสธ จึงให้ลิงก์สำหรับเปิดเอง

- [กราฟช่วง down และ restoration](https://localhost:3000/d/ai-sdwan?var-stream=live-natural-20261009-231927&from=1791575418782&to=1791575675512)
- [สรุป timing / UDP / PCAP / forecast availability](wan1-down-demo-20261010-summary.json)
- [API samples, decisions, apply events และ qdisc readback](wan1-down-demo-20261010.json)
- [ผลกลับ WAN1 และ agent readback รอบติดตาม](wan1-down-demo-20261010-followup.json)
- Grafana SQL: [ก่อน](wan1-down-demo-20261010-grafana-before.json), [ระหว่าง](wan1-down-demo-20261010-grafana-during.json), [หลัง](wan1-down-demo-20261010-grafana-after.json)

Raw UDP intervals, captures และ process logs อยู่ใน ignored Linux `.work/ml-down-demo-20261010/packet-evidence` SHA-256 ของ PCAP อยู่ใน summary ไม่เผยแพร่ raw capture หรือ runtime credentials ลง Git
