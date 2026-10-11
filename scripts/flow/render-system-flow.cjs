const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');
const root = path.resolve(__dirname, '../../artifacts') + path.sep;
const nodes = [];
const edges = [];
const add = (id,x,y,w,h,title,lines,kind='ai') => nodes.push({id,x,y,w,h,title,lines,kind});
const connect = (from,to,points,label='',kind='normal') => edges.push({from,to,points,label,kind});
const colors = {traffic:['#ecfdf5','#059669'], ai:['#eff6ff','#2563eb'], control:['#f5f3ff','#7c3aed'], warning:['#fff7ed','#c2410c'],ui:['#f8fafc','#64748b']};

add('ready',420,145,1210,115,'เปิดระบบและตรวจความพร้อม',['flexiManage 6.3.37 · 2 edges · 3 tunnels · FastAPI / TimescaleDB / Grafana','หลัง restart: Manual · ML ปิดค่าเริ่มต้น; เปิด experimental ML และ Auto แยกกัน'],'ui');
add('client',55,380,260,135,'Client LAN',['10.20.0.2','ต้นทาง traffic'],'traffic');
add('edgea',375,380,340,135,'flexiEdge A',['LAN 10.20.0.1','VPP/DPDK · multilink'],'traffic');
add('wan1',815,295,390,90,'WAN1: ไฟเบอร์',['100 Mbps · IPsec tunnel 1'],'traffic');
add('wan2',815,405,390,90,'WAN2: บรอดแบนด์',['80 Mbps · IPsec tunnel 2'],'traffic');
add('wan3',815,515,390,90,'WAN3: มือถือ',['30 Mbps · IPsec tunnel 3'],'traffic');
add('edgeb',1305,380,340,135,'flexiEdge B',['LAN 10.30.0.1','VPP / DPDK'],'traffic');
add('server',1705,380,290,135,'Server LAN',['10.30.0.2','รับและส่งข้อมูลกลับ'],'traffic');
add('natural',765,635,490,150,'Natural WAN simulation',['NetEm + background load ทุก 10 วินาที','แยกพฤติกรรม fiber / broadband / mobile','ค่าบน dashboard มาจากการวัดจริง'],'ui');

add('probe',55,875,580,175,'Collector + probe ทุกประมาณ 5 วินาที',['FastAPI เรียก flexiManage → probe บน edge A','100 ICMP/WAN → RTT mean/stddev และ loss','utilization: VPP max(RX,TX) / capacity']);
add('valid',55,1115,580,175,'ตรวจความถูกต้องและ freshness',['known WAN · ค่าถูกต้อง · อายุ ≤ 10 วินาที','ไม่รับ sample ซ้ำ / ย้อนเวลา / อนาคต','ข้อมูลเสีย: reject + audit แล้วเก็บรอบใหม่']);
add('history',55,1355,580,175,'Current metrics + history',['12 samples บน grid 5 วินาที ≈ 55 วินาที','เก็บแยก WAN / stream','ไม่เติมข้อมูลที่หายด้วย 0']);
add('gate',55,1595,580,160,'ML พร้อมหรือไม่?',['causal resample grid 5s · measured intervals 4–6s','ต้อง UP + history ครอบคลุม 55s; ไม่ extrapolate','down/gap/model/inference ไม่พร้อม → reactive']);
add('forecast',55,1820,580,190,'Features → forecast อีก 30 วินาที',['26 features: current / lag / mean / std / trend','RTT / jitter / utilization: XGBoost','loss: persistence · ไม่มี online retraining','model ยังเป็น experimental']);
add('train',55,2290,580,190,'Offline training → frozen model',['synthetic episodes: 60 train / 15 validation','split ตามเวลา + MAE gate เทียบ persistence','โหลด model และ metadata มาใช้ใน live','โมเดลช่วย policy ประเมินความเสี่ยง']);
add('audit',55,2630,580,185,'Telemetry + event audit',['current metrics / forecasts / decision reasons','proposal / approval / apply / errors','demo: UDP / ping / PCAP เป็นหลักฐาน traffic','หลักฐาน demo ไม่ใช่ input ของ ML live'],'ui');

add('profile',725,875,580,205,'FastAPI: mode + application profile',['Voice: RTT100 / jitter30 / loss1 / util90 · ≥2 Mbps','Interactive: 150 / 40 / 2 / 90 · ≥5 Mbps','Bulk: 300 / 100 / 3 / 90 · ≥10 Mbps','หน่วย: ms / ms / % / % · headroom เป็นค่าประมาณ']);
add('score',725,1150,580,205,'Policy: score ต่ำกว่าดีกว่า',['คัด fresh UP · ผ่าน SLA ก่อน; ไม่มี → least-bad','ใช้ current SLA + headroom + WAN cost','เพิ่ม penalty เมื่อ forecast แย่กว่าปัจจุบัน','forecast ไม่ลบล้างปัญหา SLA ในปัจจุบัน']);
add('decision',725,1425,580,175,'ตัดสินใจ: HOLD / SWITCH / NO PATH',['ไม่มี fresh UP → network unavailable; ไม่เขียน policy','HOLD → ใช้ priority ที่ยืนยันแล้วต่อ','ปกติ: margin≥0.15 · cooldown30s · ยืนยัน3รอบ']);
add('switch',725,1670,580,185,'SWITCH proposal พร้อมเหตุผล',['DOWN/stale → best fresh UP ทันที','SLA เสีย + healthy alternative → เปลี่ยนทันที','ไม่มีเส้นผ่าน SLA → least-bad / degraded','กรณีปกติต้องผ่าน stability guards'],'control');
add('mode',725,1915,580,145,'Control mode: Auto หรือ Manual',['Auto → ส่งให้ apply worker','Manual → รอผู้ใช้ approve / reject'],'control');
add('pending',725,2200,580,205,'Manual approval + ตรวจซ้ำ',['proposal ID / source / target / reason · อายุ30s','approve → ตรวจข้อมูลและ decision ใหม่','ยังตรงและ fresh → apply worker','reject / expired / changed → cancel; ไม่เขียน policy'],'control');
add('api',725,2630,580,185,'Control and approvals ผ่าน FastAPI',['Grafana ใช้ server proxy เรียก API','token อยู่ฝั่ง server ไม่ส่งให้ browser','เลือก mode / profile และ approve / reject','API เก็บ confirmed priority แยกจากข้อเสนอ'],'control');

add('worker',1395,875,580,150,'Apply worker: ครั้งละ 1 งาน',['asynchronous · serialize policy writes','collector และ dashboard ทำงานต่อ'],'control');
add('preflight',1395,1085,580,185,'Preflight ก่อนเขียน',['org / devices / rule / path labels ใน allowlist','ทั้ง 2 edges พร้อม · config เดิมสอดคล้อง','version/spec ตรง · ไม่มี unresolved intent','ไม่ผ่าน → error / audit; ไม่เขียน policy'],'control');
add('journal',1395,1320,580,145,'Durable intent + original snapshot',['บันทึกก่อนส่งคำสั่งไป controller','ใช้ตรวจงานค้างและการคืนค่าอย่างปลอดภัย'],'control');
add('manage',1395,1525,580,180,'flexiManage → multilink rule',['PUT: target เป็น priority แรก ตามด้วยเส้นอื่น','fallback drop · MongoDB / Redis ของ controller','แยกจาก TimescaleDB ของระบบ AI'],'control');
add('jobs',1395,1765,580,165,'Controller jobs → ทั้ง 2 edges',['ส่ง config ให้ edge A และ edge B','รอครบทุกเครื่อง · poll ทุก 1 วินาที','timeout ค่าเริ่มต้น 180 วินาที'],'control');
add('readback',1395,1990,580,175,'Readback และตรวจผลจริง',['อ่าน config กลับจากทั้ง 2 edges','priority / action / classification / flags','API ตอบรับอย่างเดียว ยังไม่ถือว่าสำเร็จ'],'control');
add('result',1395,2225,580,200,'ผล apply: ยืนยัน หรือคงสถานะเดิม',['job ครบ + readback ตรง → confirmed priority ใหม่','fail / timeout / ไม่ตรง → ไม่ยืนยัน priority ใหม่','intent ค้าง → block การเขียนครั้งถัดไป','configured priority อาจต่างจาก packet path'],'control');
add('repair',1395,2615,580,190,'Operator reconcile / rollback',['ผู้ดูแลตรวจ queue / scope / config ก่อน','ใช้ original snapshot เมื่อปลอดภัย','ไม่ retry หรือ rollback อัตโนมัติ','flexiWAN native failover อาจเกิดก่อน AI apply'],'warning');

add('db',610,2915,820,150,'TimescaleDB ของระบบ AI',['telemetry / forecasts / events / state แยกตาม stream','ข้อมูลนี้ใช้แสดงภาพและตรวจสอบการตัดสินใจย้อนหลัง'],'ui');
add('grafana',55,3250,580,225,'Grafana: https://localhost:3000',['WAN metrics / forecast / decision reason / audit','configured priority / mode / profile / ML status','Control and approvals → server proxy → FastAPI','ดูข้อมูลจาก TimescaleDB ผ่าน SQL'],'ui');
add('community',725,3250,580,225,'Community flexiManage GUI',['https://localhost:18443','Devices / Tunnels: อ่านสถานะ controller จริง','read-only · frontend ของชุมชน','หน้านี้ไม่ได้สั่ง ML approval หรือเขียน config'],'ui');
add('edgeui',1395,3250,580,225,'Official flexiEdge local Web UI',['http://localhost:18081 และ :18082','ตั้งค่าและ troubleshoot เครื่องแต่ละตัว','เป็น UI ของผู้ผลิตบน edge','แยกจาก GUI กลางของ flexiManage'],'ui');

connect('ready','edgea',[[750,260],[750,325],[545,325],[545,380]],'เริ่ม lab');
connect('client','edgea',[[315,448],[375,448]],'','traffic');
connect('edgea','wan1',[[715,448],[760,448],[760,340],[815,340]],'','traffic');
connect('edgea','wan2',[[715,448],[815,448]],'','traffic');
connect('edgea','wan3',[[715,448],[760,448],[760,560],[815,560]],'','traffic');
connect('wan1','edgeb',[[1205,340],[1260,340],[1260,448],[1305,448]],'','traffic');
connect('wan2','edgeb',[[1205,450],[1305,450]],'','traffic');
connect('wan3','edgeb',[[1205,560],[1260,560],[1260,448],[1305,448]],'','traffic');
connect('edgeb','server',[[1645,448],[1705,448]],'','traffic');
connect('natural','wan1',[[1255,700],[1280,700],[1280,340],[1205,340]],'','dashed');
connect('natural','wan2',[[1255,700],[1280,700],[1280,450],[1205,450]],'','dashed');
connect('natural','wan3',[[1255,700],[1280,700],[1280,560],[1205,560]],'','dashed');
connect('edgea','probe',[[545,515],[545,815],[345,815],[345,875]],'วัดผ่าน underlay','dashed');
connect('probe','valid',[[345,1050],[345,1115]]);
connect('valid','history',[[345,1290],[345,1355]],'ผ่าน');
connect('valid','probe',[[55,1200],[25,1200],[25,950],[55,950]],'','dashed');
connect('history','gate',[[345,1530],[345,1595]]);
connect('gate','forecast',[[345,1755],[345,1820]],'พร้อม');
connect('train','gate',[[55,2385],[15,2385],[15,1675],[55,1675]],'','dashed');
connect('profile','score',[[1015,1080],[1015,1150]]);
connect('history','score',[[635,1435],[680,1435],[680,1260],[725,1260]],'current');
connect('forecast','score',[[635,1915],[660,1915],[660,1300],[725,1300]],'','dashed');
connect('gate','score',[[635,1675],[700,1675],[700,1340],[725,1340]],'','dashed');
connect('score','decision',[[1015,1355],[1015,1425]]);
connect('decision','switch',[[1015,1600],[1015,1670]],'ควรเปลี่ยน');
connect('switch','mode',[[1015,1855],[1015,1915]]);
connect('mode','pending',[[1015,2060],[1015,2200]],'Manual');
connect('mode','worker',[[1305,1988],[1340,1988],[1340,950],[1395,950]],'Auto');
connect('pending','worker',[[1305,2280],[1365,2280],[1365,985],[1395,985]],'approve + recheck ผ่าน');
connect('worker','preflight',[[1685,1025],[1685,1085]]);
connect('preflight','journal',[[1685,1270],[1685,1320]],'ผ่าน');
connect('journal','manage',[[1685,1465],[1685,1525]]);
connect('manage','jobs',[[1685,1705],[1685,1765]]);
connect('jobs','readback',[[1685,1930],[1685,1990]]);
connect('readback','result',[[1685,2165],[1685,2225]]);
connect('result','repair',[[1685,2425],[1685,2615]],'ล้มเหลว / intent ค้าง');
connect('repair','readback',[[1975,2710],[2005,2710],[2005,2075],[1975,2075]],'','dashed');
connect('result','edgea',[[1975,2260],[2040,2260],[2040,285],[570,285],[570,380]],'confirmed policy + feedback','dashed');
connect('jobs','edgeb',[[1975,1810],[2020,1810],[2020,800],[1475,800],[1475,515]],'jobs → ทั้ง 2 edges','dashed');
connect('forecast','audit',[[635,1970],[650,1970],[650,2585],[345,2585],[345,2630]],'');
connect('decision','audit',[[725,1510],[690,1510],[690,2560],[480,2560],[480,2630]],'decision / approval / apply events','dashed');
connect('result','audit',[[1395,2370],[1325,2370],[1325,2850],[580,2850],[580,2805]],'','dashed');
connect('audit','db',[[345,2815],[345,2990],[610,2990]]);
connect('db','grafana',[[780,3065],[780,3150],[345,3150],[345,3250]],'SQL');
connect('grafana','api',[[635,3360],[675,3360],[675,2720],[725,2720]],'controls');
connect('api','profile',[[725,2700],[710,2700],[710,970],[725,970]],'','dashed');
connect('api','pending',[[1015,2630],[1015,2405]],'approve / reject');
connect('manage','community',[[1975,1600],[2060,1600],[2060,3190],[1015,3190],[1015,3250]],'read-only controller API','dashed');
connect('edgeui','edgeb',[[1975,3380],[2080,3380],[2080,755],[1620,755],[1620,515]],'','dashed');

(async()=>{
  fs.mkdirSync(root, { recursive: true });
  const browser=await chromium.launch({
    ...(process.env.FLOW_CHROME_PATH ? { executablePath: process.env.FLOW_CHROME_PATH } : { channel: 'chrome' }),
    headless: true,
  });
  try {
    const page=await browser.newPage({viewport:{width:2120,height:1200},deviceScaleFactor:1.5});
    await page.setContent('<html lang="th"><meta charset="UTF-8"><style>body{margin:0;background:white}svg{display:block}text{font-family:"Noto Sans Thai",Tahoma,sans-serif}</style><div id="root"></div></html>');
    await page.evaluate(()=>document.fonts.ready);
    const output=await page.evaluate(({nodes,edges,colors})=>{
      const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
      let s='<svg xmlns="http://www.w3.org/2000/svg" width="2120" height="3560" viewBox="0 0 2120 3560" role="img" aria-labelledby="flow-title"><title id="flow-title">AI SD-WAN complete system flow</title><rect width="2120" height="3560" fill="white"/><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/></marker><marker id="traffic-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#059669"/></marker></defs>';
      s+='<text x="55" y="65" font-size="42" font-weight="600" fill="#0f172a">AI SD-WAN — Flow ทั้งระบบในภาพเดียว</text><text x="55" y="110" font-size="24" fill="#475569">เส้นหนาสีเขียว = traffic จริง · เส้นปกติ = ข้อมูล/คำสั่ง · เส้นประ = การตรวจสอบ/feedback</text>';
      for(const [x,label] of [[55,'1. วัด WAN และพยากรณ์'],[725,'2. ตัดสินใจและอนุมัติ'],[1395,'3. สั่ง flexiWAN และยืนยันผล']])s+=`<text x="${x}" y="835" font-size="29" font-weight="600" fill="#0f172a">${label}</text>`;
      s+='<text x="55" y="3195" font-size="29" font-weight="600" fill="#0f172a">ผู้ใช้งานดูสถานะและควบคุมผ่านหน้าจอเหล่านี้</text>';
      for(const e of edges){
        const d=e.points.map((p,i)=>(i?'L':'M')+p.join(' ')).join(' ');
        const traffic=e.kind==='traffic';
        s+=`<path d="${d}" fill="none" stroke="${traffic?'#059669':'#94a3b8'}" stroke-width="${traffic?5:2}" ${e.kind==='dashed'?'stroke-dasharray="9 6"':''} marker-end="url(#${traffic?'traffic-arrow':'arrow'})"/>`;
        if(e.label){const a=e.points[0],b=e.points[1];let x=(a[0]+b[0])/2,y=(a[1]+b[1])/2-11;
          if(e.from==='pending'&&e.to==='worker'){x=1375;y=2170;}
          if(e.from==='mode'&&e.to==='worker'){x=1340;y=1900;}
          if(e.from==='decision'&&e.to==='audit'){x=365;y=2545;}
          if(e.from==='result'&&e.to==='edgea'){x=1160;y=280;}
          if(e.from==='jobs'&&e.to==='edgeb'){x=1690;y=790;}
          if(e.from==='manage'&&e.to==='community'){x=1400;y=3170;}
          s+=`<text x="${x}" y="${y}" font-size="21" text-anchor="middle" fill="#475569" stroke="white" stroke-width="7" paint-order="stroke">${esc(e.label)}</text>`;
        }
      }
      const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');
      const overflows=[];
      for(const n of nodes){
        const c=colors[n.kind];
        s+=`<g id="node-${n.id}"><rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="12" fill="${c[0]}" stroke="${c[1]}" stroke-width="2"/>`;
        ctx.font='600 28px "Noto Sans Thai", Tahoma';
        if(ctx.measureText(n.title).width>n.w-34)overflows.push(n.id+': title');
        s+=`<text x="${n.x+18}" y="${n.y+40}" font-size="28" font-weight="600" fill="#0f172a">${esc(n.title)}</text>`;
        ctx.font='24px "Noto Sans Thai", Tahoma';
        for(let i=0;i<n.lines.length;i++){if(ctx.measureText(n.lines[i]).width>n.w-34)overflows.push(n.id+': '+i);s+=`<text x="${n.x+18}" y="${n.y+77+i*31}" font-size="24" fill="#334155">${esc(n.lines[i])}</text>`;}
        s+='</g>';
      }
      s+='</svg>';document.getElementById('root').innerHTML=s;return{svg:s,overflows};
    },{nodes,edges,colors});
    if(output.overflows.length)throw new Error('Text overflow: '+output.overflows.join(', '));
    fs.writeFileSync(root+'ai-sdwan-system-flow.svg',output.svg,'utf8');
    await page.locator('svg').screenshot({path:root+'ai-sdwan-system-flow.png',timeout:60000});
    console.log(JSON.stringify({nodes:nodes.length,edges:edges.length,width:3180,height:5340,pngBytes:fs.statSync(root+'ai-sdwan-system-flow.png').size}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
