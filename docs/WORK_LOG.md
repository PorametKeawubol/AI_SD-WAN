# บันทึกการทำงาน — 1 ตุลาคม 2026

## ข้อมูลตั้งต้นและการตัดสินใจ

- Workspace `E:\Proposal\AI_SD-WAN` ว่าง จึงสร้างprojectใหม่ ไม่มีโค้ดเดิมที่ต้องรักษา
- อ่านproposalจากDOCXด้วยOOXML text extraction โดยไม่แก้ต้นฉบับ: เป้าหมายtelemetry/ML/pathselection, Auto/Manual, auditlog, monitoringและevaluation
- เก็บส่วนFastAPI/Python/XGBoost; ขยาย2WANเป็น3WAN; ใช้SQLiteและdashboardที่มากับserviceเพื่อให้รันได้โดยไม่ต้องตั้งinfraก่อน
- `docker info`เชื่อมDockerDesktopLinuxEngineไม่ได้ ไม่มีcontroller/edge identifiersหรือtelemetryจริง จึงใช้simulatorและdryrunพร้อมระบุขอบเขตตรงไปตรงมา
- 16faultsเป็นcatalogueที่กำหนดสำหรับlab ไม่ได้สมมติว่าเป็นจำนวนปัญหาทั้งหมดของ3links หรือว่าองค์กรขนาดกลางส่วนใหญ่ใช้3WAN

## การพัฒนาและแก้แนวทาง

1. สร้างschema, featurehistory, applicationSLA และpathpolicy แยก “propose” ออกจาก “applyverified”
2. สร้างsynthetictrainingepisodesที่สุ่มfaultprocessแยกจากbenchmarkcases ไม่ใช้scenarioIDเป็นfeature
3. โมเดลแบบทำนายค่าตรงด้วยsquarederrorรอบแรกมีvalidationMAEสูงกว่าpersistenceหลายmetric จึงปรับเป็นresidualpredictionด้วยabsoluteerrorและvalidationgate≥2%ก่อนใช้แต่ละmetric
4. ตรึงโมเดลสุดท้ายจากtrain60episodes/validation15episodes: latency/jitter/utilizationผ่านgate ส่วนlossใช้persistence
5. เพิ่มSLA-compliantcandidatefilter เพื่อไม่ให้forecastที่มองhealthyWANในแง่ร้ายผลักไปเลือกWANที่ผิดSLA; เพิ่มconfirmationที่ต้องมีsampleใหม่และelapsedtimeจริง
6. สร้างSQLitepersistence, watchdog, manualexpiry/revalidation, autoapply, audit eventsและfail-closedadapter boundary
7. สร้างFastAPIdashboardและCLIสำหรับtrain/eval/report/serve/replay พร้อมBearer token/originchecks
8. รันdevelopment smokeและexploratoryblock50000 แล้วเก็บไว้ต่างหาก ไม่ใช้เป็นfinalheld-outresult
9. ใช้finalseedblock60000–60029โดยโมเดลเดิมและpolicyเดิม ไม่ปรับโมเดลหรือเกณฑ์ผ่านตามfinalresults
10. สร้างCSV/JSON/Markdownreport, Wilson/bootstrapuncertainty, วิเคราะห์early-switchของS12 และmatplotlibplot

## Validation ที่ทำ

- Unit/integration testsของschema/policy/service/API/ML/eval: รวม38tests ผ่านก่อนfinalrun รวมUp/Downทั้ง8combinations
- ตรวจdown/stalebypasscooldown, brownoutescape, hysteresis/confirmations, future/replay/NaN/rangeerrors
- ตรวจmanualapprove/reject/expiry/replay/networkchange/modechange, failed/unverifiedapplyไม่advanceconfirmedroute และreloadstateหลังrestart
- ตรวจall-downมีavailability0และไม่นับเป็นconnectivityrecovery; priorityที่ปล่อยbrownoutไม่recoverต้องfailและมีrecoverydelayจริง
- ตรวจcausalfeatures, missinghistory/gaps, train/loadroundtrip, validationgateและmodelchecksum
- Ruffcheckและformatผ่าน
- Localserverเริ่มได้, `/health`รายงานdry_runและmodel_loaded=true
- Browserruntimeเริ่มได้แต่ไม่มีconnectedbrowser (`browsers.list()`ว่าง) จึงไม่อ้างว่าผ่านvisual/interactivebrowserQA ใช้TestClientและHTTPchecksสำหรับAPI/staticassetsแทน

## การตีความผล

ดูfinalnumbersจาก `reports/latest/EVALUATION.md` และ `results.json` โดยตรง ไม่คัดผลexploratoryมาเป็นfinal ระบบreactiveและAIรับมือcasesได้เท่ากันในชุดนี้ ดังนั้นยังไม่มีหลักฐานว่าMLเพิ่มscenario-coverageหรือSLAattainmentเหนือreactiveที่ออกแบบดี

MAEต้องรายงานแยกจากroutepass; validationที่ดีขึ้นไม่รับประกันheld-outfaultMAEที่ดีขึ้น และearly-switchจากreactivequalityscoringก็เกิดได้โดยไม่มีforecast

## สิ่งที่ยังต้องทำเพื่อก้าวพ้น prototype (อัปเดตหลังเพิ่ม flexiWAN)

- รัน flexiEdge/VPP จริงและยืนยัน compatibility: controller/schema 6.3.37 เชื่อมจริงแล้ว, job polling/read-back/reconcile/rollback implement แล้ว แต่ device jobs ทดสอบได้เพียง protocol fixtures จน Linux พร้อม
- รวบรวมnetworktelemetryจริงให้ตรงcadence/unitsและทดสอบmodeldrift/generalizationบนholdoutใหม่
- Execute 3WAN testbed/live runner ที่เตรียมไว้ เก็บ UDP/PCAP/audit และเปรียบเทียบ native QBR; ยังไม่มีผลแพ็กเก็ตจริง
- ทดสอบdashboardด้วยbrowserจริง รวมmobilelayoutและuserworkflow
- หากหลายsite/workers: ย้ายtime-seriesDB, แยกworkers, locking/outbox, authที่มีroles และretentionpolicy

## หลักฐานส่งมอบรอบสุดท้าย

Final seeds 60000–60029: 7,650 trials. Guarded AI รับมือได้ **16/16**, กู้/รักษา SLA ได้ **15/15** กรณีที่ยังมีเส้นทาง; S07 เป็นการตรวจ no_path เท่านั้น Reactive และ linear-trend ผ่าน 16/16 เช่นกัน ส่วน priority ผ่าน 10/16 จึงยังสรุปความเหนือกว่าด้าน SLA ของ AI ไม่ได้

Held-out MAE (deployed forecast / persistence): latency 29.5202/26.1756 ms, jitter 5.9906/5.3581 ms, loss 2.2618/2.2618 percentage points, utilization 2.4470/2.7554 percentage points ใน S12 guarded AI เปลี่ยนก่อน WAN1 ผิด SLA เฉลี่ย 37.39 วินาที เทียบ reactive 33.67 และ linear-trend 37.11 วินาที; ค่านี้มาจาก simulator ที่มี polling resolution 5 วินาที

`scripts/verify_local.py` ทดสอบกับ HTTP server จริงที่ localhost แล้วผ่าน: static assets HTTP 200, manual Reject คง route, Approve เปลี่ยน simulated route, Approve ซ้ำ HTTP 409, และอ่านผล eval 7,650 trials ได้ เก็บหลักฐานใน `reports/verification.json`

Package ติดตั้งแบบ editable สำเร็จ, CLI `sdwan scenarios` ทำงาน, JavaScript syntax check ผ่าน, Ruff ผ่าน และ `pip check` ไม่พบ dependency conflict กราฟ `reports/latest/evaluation.png` ถูกเปิดตรวจและแก้ตำแหน่ง legend แล้ว ไฟล์ proposal ต้นฉบับยังมีขนาดและเวลาบันทึกเดิม

## เพิ่ม flexiWAN ตามคำขอ “ทำให้ครบเลยดิ flexiWAN ด้วย”

1. ตรวจและลอง restart WSL2/Docker ตามคำตอบผู้ใช้แล้ว แต่ Linux ยังเริ่มไม่ได้: `HCS_E_SERVICE_NOT_AVAILABLE`, CPU firmware virtualization=true, HypervisorPresent=false, VirtualMachinePlatform disabled, WSL feature enabled และ process ไม่ใช่ Administrator DISM ตอบ error740/bcdedit access denied จึงเตรียม `scripts/enable-wsl2.ps1` ให้รันจาก Administrator โดยไม่ reboot เอง หลักฐาน redacted ใน `reports/flexiwan/environment.json`
2. ทำงาน controller ต่อด้วย native fallback บน Windows: official flexiManage source tag6.3.37 commit62a168daedc785706808c7be2689e208d81bf77e, Node24.14.1, portable Mongo6.0.29 replica set, RedisWindows7.2.12 และ local SMTP sink ทุก listener จำกัด127.0.0.1 ไม่มีการติดตั้ง Windows service และไม่มีอีเมลภายนอก
3. ติดตั้ง upstreamด้วย `npm ci --omit=dev --ignore-scripts` พบ56advisories จึงคง dependency lock ของ upstreamไว้ใน isolated lab ไม่ใช้ forced audit fix แล้วอ้างว่าสอดคล้อง schema เดิม เปลี่ยน ENVIRONMENT เป็นisolated-labเพื่อไม่เรียก development Mongo UI ที่ไม่ได้ติดตั้ง
4. สร้าง RSA3072 self-signed lab CA 90วัน เปิด TLS verification ตลอด เก็บ key/database/mail/private logs ไว้ใน ignored `.work/native/` Seed default notification migration ที่ organization creation ต้องใช้ และ seed empty imported-app catalog แบบ setOnInsert โดยไม่ทับ catalog เดิม เพราะ upstream policy installation สมมติว่ามี document นี้
5. ใช้ APIจริงลงทะเบียน/verify/login/createorg/organization-scoped manager access key/สาม Tunnel labels/dedicated policy/stable rule ID/enrollment token เก็บ secrets ใน credentials.json ที่ ignored ไม่พิมพ์ secretsลงlog ตอน bootstrap ครั้งแรกมี notification seed ขาดและ password checkpoint ไม่ครบ จึงแก้ seed และ recover เฉพาะ account lab ผ่าน local reset mail แล้วปรับ scriptให้ checkpoint/resume ได้
6. Export OpenAPI จริงไป `contracts/fleximanage-6.3.37.openapi.yaml` พร้อม upstream AGPL license ตรวจ canonical LF SHA256=4a95b529264a201d083176dfeada3707add1d5ceeba17a01518ab9a733466664 ก่อน adapter writes และตรึง source commits ใน `lab/upstream-lock.json`
7. Implement `FlexiWANAdapter` จริง: strict scope/HTTPS/accesskeyenv, pagination, connected+approved+running+installed preflight, jobs exact machine IDs, agent configuration readback ทั้ง action/classifier, durable intent snapshot, OS file writer lock, no blind retries และ reconcile/explicit rollback ที่ไม่ทับ concurrent admin changes
8. แยก real apply ไป background worker1ตัวเพื่อไม่บล็อก telemetry/UI; statusเป็นinflightจน jobs/readbackสำเร็จจึงเลื่อน configured priority เก็บ actual completion timestamp/duration; testยืนยัน delayed/failedadapterไม่advance stateก่อนverify
9. Implement VPP probe/collector RPC: ICMP100packetsต่อWAN+rx/txcounter deltas, RTT/stddev proxy, %loss/utilization, missing/corrupt rowsquarantine ไม่เติม healthy zero ตั้งต้นliveเป็นManual/reactiveและปิดโมเดลsyntheticจนมีrealvalidation ปิดAPItelemetryreplayบนlive
10. สร้าง Linux recipe: DockerComposecontroller+RSinitialization+Redis+Mailpit,9libvirtnetworks,2Ubuntu20.04edgeVMs/NIC5ใบ/fixedMAC/cloud-init,3isolatedWAN namespaces+LANclient/server,netem16cases+CONTROL,provisionผ่านAPIจนVPP/tunnels/policy/probesverifyจริงจึงเขียนconfig
11. ตรวจcurrent focal package index ไม่พบedge6.3.37 จึงตรึง binary6.3.35 พร้อมSHA/GPGfingerprints/packagehold และบันทึกcompatibilityยังpendingไม่อ้างว่าทดสอบแล้ว Upstreamsystemcheckerคืน0ได้แม้บางcheckfailจึงใช้routerrunning/readback/probechecksเป็นหลักฐาน
12. ตรวจDockerregistryจริงพบmongo6.0.29tag404 แก้Composeเป็นofficial6.0.28 แล้วตรึงNode/Mongo/Redis/Mailpitmanifestdigestsและตรวจlinux/amd64 เก็บใน `lab/container-lock.json`; Composeconfigตรวจผ่าน แต่imagebuild/startยังblockedจากDocker
13. ImplementactualUDPecho/pacedtraffic,netemload98Mbpsผ่านfixedWAN1underlay,PCAPsnaplen160,raw1smetrics/APIstates/auditSQLite/cleanup และliveaggregatorพร้อมinvalidcounts/Wilson95%CI Default576trialsอย่างน้อย19.2h,finalexperimentควร30repeatsและrealholdout
14. เพิ่มnativeQBRbaselineโดยใช้ explicit custom UDPapp classifiers3profileเดียวกันทุกstrategy ControllerจริงทดสอบPOSTapps/PUTQBR/GET/restoreผ่าน; ตอนแรกPOSTไป `/appidentifications/custom` ได้405 จึงตรวจschema/sourceแก้เป็น `/appidentifications` และใช้ชื่อformat `custom:ai-lab-*` ตามvalidator ผลยังไม่ใช่QBRpacketperformance
15. สร้าง `scripts/verify_fleximanage.py` ทดสอบกับ controllerจริงที่ไม่มีedge: version/schema, priorityswap/readback/stableID/restore, classifiercreation/idempotentlookup, QBRPUT/GET/restore,invalidkey401และpreflightปฏิเสธmissingedge หลักฐาน `reports/flexiwan/api-verification.json` ระบุ real_edge=false/packet_forwarding_tested=false

## การตรวจรอบ flexiWAN และสถานะที่เหลือ

- Suiteรอบก่อน64tests ครอบคลุมprotocoljobs/readback/unknownwrite/rollback/concurrentchanges/pagination,probeparsers/partialrows,OSwriterlock,actualUDPloopbacksockets,netemcatalogue,networkUUIDscopeและnativeQBRobserverที่ห้ามเปิดAutoหรือreplay ปัญหาassertเก่าที่เลื่อนเข้าnewtestจากการแทรกpatchถูกแก้ก่อนfinalrun
- Ruffformat/check, Pythonpackage/CLI/pipcheck, JSsyntax และComposeconfigurationตรวจแล้ว ไม่rerun7,650synthetictrialsเพราะmodel/policy/evaluationcriteriaไม่เปลี่ยน
- Browserยังไม่มีconnectedinstance จึงยังไม่อ้างinteractivevisualQA
- Runbookแก้dependencyorderให้สร้างmanagementnetworkก่อนbindcontroller/bootstrap แล้วจึงใช้enrollmenttokenสร้างVM; reusable networks ต้องUUID/bridge/subnetตรงกับlabและactive ไม่ทับVMเดิม
- ผลpacket/nativeQBR/edge6.3.35compatibilityยังpendingทั้งหมด ไม่สร้างfabricatedliveCSVหรือยืมsyntheticresultsมาใส่live
- ขั้นต่อไปที่ต้องเปลี่ยนexternalstateจริง: AdministratorเปิดVirtualMachinePlatformและrestartWindows หรือใช้Linuxlabhostพร้อมlibvirt จากนั้นexecuterunbook/provision/smoke/liveeval และเก็บrealtraining/validation/testก่อนสรุปประโยชน์ML

ตรวจfinalrunbookเพิ่มแล้วปรับwarmup20เป็น70วินาทีเพื่อให้history12freshsamplesครบก่อนfault: finalplan576trials×170วินาที=อย่างน้อย27.2h เพิ่มforecastreadiness/healthybaselinegate,แยกSQLiteทุกtrialผ่าน `serve --database`,เก็บbaselinepolicy/selectedapplyjournals/config/modelmetadata และตรวจSSHhostkeysในknown_hostsไฟล์เดียวกันทั้งuser/rootโดยไม่ปิดverification Targetedlab/service21testsและlab6testsผ่านหลังปรับ และupdateddashboardHTTPverifyผ่านอีกครั้ง

เอกสารต้นฉบับไม่เปลี่ยน รุ่นimplementationยังเปิดให้ปรับarchitecture/model/thresholdได้บนexperimentใหม่พร้อมvalidationและholdoutใหม่ รายละเอียดcommands/definitionsอยู่ใน `docs/REAL_TESTBED.md` และสถานะอ่านได้ใน `reports/flexiwan/STATUS.md`

## Follow-up: เปิด Windows virtualization จริง — 1 ตุลาคม 2026

- ผู้ใช้ให้ลองดำเนิน flexiEdge/packet measurement ต่อ ตรวจสถานะใหม่แล้ว sandbox บางคำสั่งเริ่ม process ไม่ได้ (`helper_unknown_error: setup refresh had errors`) จึงขออนุญาต elevated diagnostics ตามข้อจำกัด environment
- Elevated diagnostics ยืนยัน Administrator=true, VirtualMachinePlatform disabled, HypervisorPresent=false และ Docker Linux Engine ตอบ500 จึงตรวจเนื้อหาสคริปต์เดิมแล้วขออนุญาตรัน `scripts/enable-wsl2.ps1` เพื่อเปิด feature จริง
- สคริปต์รันสำเร็จ exit0: WSL feature ไม่ต้อง restart, VirtualMachinePlatform รายงาน RestartNeeded=true และ bcdeditตั้งhypervisorlaunchtypeautoสำเร็จ ไม่ได้ reboot เครื่องอัตโนมัติ
- ตรวจซ้ำด้วย `scripts/check-wsl2.ps1`: WSL Enabled, VirtualMachinePlatform **EnablePending**, CBS servicing reboot pending, HypervisorPresent=false บันทึก `reports/flexiwan/wsl2-readiness.json` และ `wsl2-repair.json` แยกจาก environment.json รอบก่อนเพื่อรักษาหลักฐานเดิม
- อุปสรรคเรื่องสิทธิ์ Administrator ถูกแก้แล้ว ขั้นที่ยังต้องเปลี่ยนบนเครื่องคือ restartWindows เพื่อactivatefeature ก่อนWSL2/Docker/Linuxlab จะเริ่มได้ จึงยังไม่มีedge/packetresultจริง
- ให้ agent ช่วย review Linux lab แบบread-onlyระหว่างเปิดfeature พบจุดที่ failedtrialอาจไม่เก็บstates/error,cleanupอาจmaskprimaryerror,outputอาจreusehistoryและsetupfailureถูกติดlabelreal_packets จึงแก้runner/เพิ่มtestsก่อนดำเนินactualexperiment
- แก้ `lab/run_live_eval.py` ให้สร้าง freshtrialdirก่อนcontrollermutation,checkpointstates/phase/errorและbaselinejournalแม้applyล้ม,รักษาprimaryerrorแยกcleanup/persistence,capture Exceptionที่experimentboundaryแล้วsaveinvalid/stopโดยไม่จับKeyboardInterrupt/SystemExit ไม่มีblindretry
- ตรวจactualpacketartifactsด้วยUDPoutputschemaและPCAPheader/record จึงยกระดับเป็นreal_packets;setupfailureเป็นlive_lab_attempt มีpacket_measurement_completedเพื่อไม่ให้partialsetupได้passcredit Reporterเก็บinvalidrowsและไม่เอาผลsimulationมาใช้ แยกmetadatafixturesออกจากหลักฐานlabจริง
- เพิ่ม14runnerfixturetestsและ4reportfixturetests รวมเต็มsuite **82 passed** ใน11.35วินาที มีStarlette/httpxdeprecationwarningเดิม1รายการ JUnitอยู่ `reports/flexiwan/tests.xml` Ruffformat/checkผ่านหลังแก้intentionalcleanupExceptionannotationsและบันทึกsecondaryartifacterrorแทนsilentpass
- ยังไม่ได้เริ่มflexiEdgeหรือactualpacketexperiment ไม่มีlivepass/throughput/MAEที่อ้างว่าexecuteแล้ว ขั้นต่อไปคือบันทึกงานและrestartWindowsเพื่อactivatefeature จากนั้นตรวจWSL2/Docker/Linuxcapabilitiesและดำเนินrunbook

## หลัง restart: hypervisor, KVM edges และแพ็กเก็ตจริง — 1–2 ตุลาคม 2026

- ตรวจ boot/readiness ใหม่: VirtualMachinePlatform/WSL Enabled, HypervisorPresent=true, servicing reboot pending=false สถานะ EnablePending แก้แล้ว เปิด Docker Desktop โดยคง projects เดิมไว้
- ติดตั้ง distro เฉพาะ `AI-SDWAN-Lab` Ubuntu24.04.5 ที่ `.work/wsl/`, libvirt/QEMU/Docker Engine/Python3.12 และ project `/opt/ai-sdwan-lab` ตรวจ nested AMD `/dev/kvm` ใช้งานได้
- Build/start pinned controller6.3.37/Mongo6.0.28/Redis7.2.12/Mailpit1.27.8 จริง Bootstrap verifiedTLS/localaccount/org/labels/policy/token ใช้ credentials เฉพาะLinux แก้Mailpitให้เข้าbackendและmanagementnetworkเพื่อให้publishedloopbackportทำงาน
- ตรวจ focal image release20250624 SHA256=`18f2977d77dfea1b74aee14533bd21c34f789139e949c57023b7364894b7e5e9` สร้าง KVM edgesสองเครื่อง4GB/2vCPU/5NIC ติดตั้ง pinned6.3.35 ผ่านcloud-initและenrollสำเร็จ ตรวจSSHkeysเทียบfingerprintsจากVMconsoleก่อนtrust
- WSLปิดdistroหลังshellจบแม้systemdทำงาน จึงใช้hiddenkeepalive ก่อนสร้างVMซ่อมเฉพาะ9verifiedemptyinactivebridgesที่ค้างอยู่โดยตรวจไม่มีVM/attachedlinks ไม่ลบdefinitions ต่อมาเพิ่มstart-wsl-lab.ps1/lab.resume ตรวจnetworkUUID/subnet,VMdisks/NICs,namespacepeerก่อนresume ทดสอบactive labผ่าน ยังไม่ได้coldrebootอีกครั้งหลังสร้างVM
- แก้libvirtXMLwhitespacecomparisonและiproute2 `link set dev a/b` สร้าง3WAN/2LANnamespaces/netemครบ Actualprovisioningแก้WANroutingเป็นNONE/LAN+tunnelOSPF,GETsinglejobที่คืน{}เปลี่ยนเป็นfilteredbatchjobs+exactIDs/machinetargets,สร้างtunnelทีละWANเพื่อไม่raceupstreamcounter และไม่ใช้JSONkeyorderผูกMAC/capacity
- ตรวจVPPcoreพบnativecryptopluginabortระหว่างIPsecSAadd ปิดcrypto_native/crypto_ipsecmbให้ใช้OpenSSLพร้อมbackupstartup.conf ไม่upgradebinary Cloud-initใหม่รวมfallbackเดียวกัน ผลthroughputอ้างเฉพาะconfigนี้
- Start/tunnel/policyjobsสำเร็จทั้งสองedge agentreadbackตรงaction/classifier/flags normalizeเฉพาะemptywildcardsที่agentเติม เพิ่มnegativechecksสำหรับnonemptyclassifier/flagmismatch
- vppctlexitSIGPIPEหลังoutputครบจึงคงignoredSIGPIPE ส่วนVPPpingได้0receivedทั้งที่tcpdumpเห็นmatchingrequests/replies เปลี่ยนprobeเป็นLinuxpingbindverifiedTAPkind/MACแต่แพ็กเก็ตผ่านVPP อ่านVPPcounterdeltas ต้องครบ100sentและไม่เติมhealthyzeroเมื่อmissing เพิ่มintegrationtestจับparsedtuplepipelinebug
- Provisionตรวจ3WANprobes/readbackจริงก่อนสร้างconfig/flexiwan.local.jsonและedge-provisioning.json MappingมาจากactualVPPaddresses มี3labeledtunnelsใช้งานจริง
- ExecuteUDP1250bytes/voice2Mbpsจริง5trials×170s พร้อมWANPCAP160snaplen/states/SQLiteaudit/baselinepolicy/journals: S01reactivefail(SLA95.7%,recovery13s),S01nativeQBRfail(84.3%,3sแต่มีoutageตามมา),S01nativepriorityfail(70.0%,31s),S01experimentalforecastpass(100%,2s),S08reactivepass(100%,3s) ทั้ง5valid ไม่ลดเกณฑ์98%หรือลบfailedtrials
- Forecasttrialโหลดโมเดลจริงและผ่าน3WANwarmupgate แต่ยังเป็นโมเดลฝึกsynthetic ไม่ใช่real-datavalidation หนึ่งtrialไม่พิสูจน์AIเหนือกว่าbaselines ยังต้องrepeats/investigateoutagesก่อนbenchmark576trials/realtrain-validation-heldout
- เปิดdashboardlocalhost8000 backend=flexiwan/manual/model_loaded=false,currentpriorityWAN2,realtelemetryครบ3WAN ตรวจHTTPpage/health/stateจากWindowsได้ ไม่อ้างinteractivebrowserQA เพิ่มhelperstart/stopและrunnerปฏิเสธport8000/8001ที่มีserviceเพื่อกันpolicywritersซ้อน
- Fullsuiteปัจจุบัน109passedทั้งWindows19.89sและLinux9.85s Ruffcheck/formatผ่าน49files มีStarlette/httpxdeprecationwarningเดิม1รายการ เก็บcurrentJUnitแยกจาก82testsรอบก่อน Exportpublicevidenceหลังscancredential/privatekey ไม่exportsecrets/core/privatecontrollerlogs
- WindowsAPIhistory/syntheticreport/proposalต้นฉบับคงอยู่ ผลและข้อจำกัดล่าสุดอยู่reports/flexiwan/LIVE_BRINGUP.md,post-restart-verification.jsonและSTATUS.md

## ทำต่อหลัง restart — 2 ตุลาคม 2026

- ตรวจ readiness ซ้ำ: VirtualMachinePlatform/WSL Enabled, HypervisorPresent=true และไม่มี servicing reboot pending ใช้ lab เดิมทั้งหมด
- รัน S01 เพิ่มด้วย timers เดิม พบ reactive และ native priority ยังมี outage ส่วน QBR สองรอบที่ recovery=0 ใช้ WAN สำรองอยู่ก่อนฉีด fault ตาม PCAP จึงไม่ตีความว่า failover ทันที เก็บสี่ completed trials และ forecast warm-up failure ไว้ครบ
- ตรวจ FRR ของทั้งสอง edges พบ tunnel hello/dead 10/40 วินาที จึงเพิ่ม helper ที่ตรวจ hostname, management MAC/IP, tunnel subnet, cost/area และ SSH identity ก่อนเปลี่ยนเฉพาะสาม tunnel เป็น 1/4 วินาที ทั้งสองฝั่งใช้ค่าเดียวกันทุก strategy เก็บ backup เดิมและ verify actual adjacencies หลัง apply
- เพิ่มการบันทึก source port/IP และ actual wall start ของ UDP generator ใช้ port42000+repetition เหมือนกันระหว่าง strategies เพื่อคุม flow hashing บันทึก model/source SHA-256 พร้อม clock/routing readback ใน experiment metadata
- Forecast warm-up เดิมปฏิเสธ measured cadence ที่คลาดจาก5วินาทีเล็กน้อย เพิ่ม causal resampling สำหรับ experimental live inference โดยใช้เฉพาะข้อมูลที่รับแล้ว รับ intervals4–6วินาที ไม่เติม gaps/down หรือ extrapolate และรักษา raw timestamps เดิม Normal service ยังใช้ strict contract และปิด synthetic model
- Windows sleep/hibernate ขัดจังหวะ revised batch จริง: guest clocks ตามหลัง WSL ประมาณ27922วินาที หนึ่ง packet trial underdelivered และ preflight ถัดไปล้ม จัดเป็น invalid เก็บ Windows event evidence และ repair เฉพาะ guest realtime หลังยืนยัน identity โดยไม่เปลี่ยน Windows clock
- เพิ่ม runner guards สำหรับ wall/monotonic clock discontinuity และ scheduler gap>12วินาที พร้อม Windows wrapper ที่ถือ ES_SYSTEM_REQUIRED เฉพาะระหว่างคำสั่ง ตรวจ powercfg /requests เห็น request จริง Wrapper ปล่อย request ใน finally และไม่ปิด explicit sleep/lid actions
- เพิ่ม production run helper ที่อ่าน credentials ภายใน Linux ตรวจ guest clocks/routing ก่อนเริ่ม และเก็บ read-only agent/FIB/OSPF observations หลัง state samples โดยใช้ strict host keys ไม่เปิด policy writer ซ้อน
- ชุด S01 หลัง clock recovery เสร็จครบ12 trials: ทั้ง native priority, native QBR, reactive และ experimental synthetic forecast ผ่าน3/3 แต่ละกลุ่ม SLA หลัง grace=100% recovery2–4วินาที เกณฑ์ SLA และ weights เดิมไม่เปลี่ยน ไม่สรุปว่า AI เหนือกว่า baseline
- เริ่ม reactive voice coverage ของ S02–S16 อีก15 trials ใช้ source/weights ชุดเดียวกัน ผล invalid และ routing failure จะคงอยู่ในรายงานแยกจาก successful trials
- เพิ่ม PCAP direction/phase analyzer ที่อ่าน validated classic-PCAP records แยก encrypted frame counts ออกจาก UDP echo delivery และระบุ timing uncertainty เพิ่ม frozen t+30-second forecast diagnostic เทียบ persistenceโดยไม่ฝึก/promote โมเดล ตัด unavailable RTT/stddev sentinels และไม่ใช้ข้อมูลอนาคตใน features
- Isolated tool diagnostic ใช้ actual focal iputils binary พบ cold ARP ภายใต้100%lossทำให้100-packet probeเกิน4วินาทีได้ ลบเฉพาะ temporary diagnostic namespaces ใน finally ไม่แก้ namespaces ของ flexiWAN และไม่เรียกผลนี้ว่า edge forwarding evidence
- Full suite132 passed ทั้ง Windows13.89s และLinux9.72s; Ruff check/format ผ่าน มี Starlette/httpx deprecation เดิมหนึ่งรายการ เก็บ JUnit แยกจากรอบ109 tests
- ปรับ README, runbook และ restart guide ให้ตรวจ clock/tunnel timers ก่อนเริ่ม dashboard หลัง boot/reconfiguration เก็บรายงาน continued trials และกราฟ PNG/PDF แยกจากรายงาน bring-up แรก Public exports ตรวจ credential/private-key leakage ก่อนคัดลอก

- ผล continuation สรุป: 45 attempts, 43 completed packet trials, 40 valid, 33 passed, 5 invalid attempts; reactive voice มี valid observations 16/16 และ passing observations 14/16 เคสที่ไม่ผ่านยังเป็น S07, S12 คืน dashboard Manual/reactive priority WAN2 พร้อมสาม WAN สดและ LAN ping3/3 ดู CONTINUATION.md และ continuation-verification.json


## Status/resume verification — 2026-10-02 13:20 Asia/Bangkok

- HypervisorPresent remains true. WSL was stopped and the previous keepalive had exited; restored the owned existing lab without creating new VMs, enrollment or controller data.
- Default `/run/docker.sock` reached Docker Desktop 28.5.2, so the first resume stopped because the native seeded controller image was absent there. Installed a distro-scoped systemd Docker socket override for `unix:///run/ai-sdwan-docker.sock`, verified native Engine 29.1.3 with its original `/var/lib/docker`, and made resume select it explicitly.
- Removed only the four newly created, never-started Desktop partial containers and their two newly created unused volumes after verifying exact IDs, creation times, labels and state. Native lab volumes and other Desktop projects were preserved; downloaded shared images were retained.
- Both existing KVM edges reconnected; strict agent policy readback passed, unresolved intents=0, three tunnels were configured and all six OSPF adjacencies retained hello/dead 1/4s.
- Found a subsecond guest lead (~0.13s at receipt) rejected by strict future-timestamp checks. Added explicit `--force-sync` to the identity-checked clock helper and repaired clocks while policy services were stopped; normal read-only/threshold behavior and telemetry acceptance remained unchanged.
- Actual Windows HTTP checks passed for page, health, state and both assets. Dashboard Manual/WAN2, ML disabled, three fresh healthy WANs; final LAN ping 3/3. Initial post-start LAN probe received 1/3 and was preserved in tool output; the subsequent two checks each received 3/3.
- Targeted resume/clock tests: 9 passed on Windows and Linux; Ruff check/format passed for the changed Python files. Earlier full-suite 132-test evidence remains historical.
- New current evidence uses `status-resume-20261002-*` files, preserving earlier experiment reports and final snapshots. No new experiments, full 576-trial run or real-data ML validation occurred. S07/S12 remain failed.
- Verified stopped-WSL-to-running lifecycle; a further Windows cold reboot after VM creation was not performed.

- Follow-up readback found intermittent stale dashboard data despite forced startup synchronization. Guest systemd-timesyncd remained active against Ubuntu NTP; the pre-fix 30-second monitor is retained rather than counted as stable telemetry.
- Collector now waits at most one second for host realtime to reach a raw sample timestamp before the unchanged strict freshness validation. Raw timestamps are preserved; larger future leads, stale/corrupt values and backward clock steps remain rejected. Added boundary/backward-clock tests; 59 related tests passed on Windows (19.11s) and Linux (3.85s), with the existing Starlette/httpx deprecation warning. Ruff check/format passed for all seven changed Python files.
- Final controller/agent/LAN and Windows HTTP verification at 13:32 Bangkok passed. All 60 consecutive one-second dashboard observations after the collector fix had three fresh healthy WANs. Final snapshots and both before/after monitors use distinct evidence filenames; frozen experiments and model weights remain unchanged. This validates one observed resume and a one-minute telemetry interval, rather than arbitrary future reboot/suspend behavior.


## 2026-10-02 — requested stack migration

Replaced the production SQLite store with TimescaleDB 2.30.2/PostgreSQL 17 and replaced the custom monitoring UI with Grafana 13.2.3. FastAPI/Python, flexiWAN/flexiManage/flexiEdge, REST, SQL and XGBoost remain the application stack. Both images use verified digest pins and the native lab Docker endpoint. Grafana metadata also uses PostgreSQL; its telemetry datasource is SELECT-only.

Moved 6,384 telemetry / 2,287 events / 2 state rows with exact canonical content SHA-256 equality. Archived legacy operational SQLite/WAL/SHM and removed old static dashboard/HTTP verifier; original reports, packet captures, model weights, controller credentials and VM identities were preserved. Future trials use separate SQL streams and JSON audit exports.

Enabled native API/Grafana HTTPS and PostgreSQL verify-full TLS. Serve requires a Bearer token and TLS keys even on localhost. Swagger docs use locally pinned assets with a CSP nonce and Authorize support. Windows CurrentUser Root trusts the lab CA; private login/token files stay in ignored runtime.

146 tests pass without skips on both Windows and Linux, including actual TimescaleDB tests; 15 Windows API/service checks passed after the Swagger update. Ruff check/format passed. Actual Grafana queries for all 14 SQL panels plus stream selector, reader permissions, HTTPS assets, missing-token/cross-origin/injection rejection, both agent configurations, three tunnels and LAN ping 3/3 were verified. Idempotent resume succeeded and Windows observed all three WANs fresh over 60 seconds.

Interactive visual QA could not run: native UI pipe unavailable and tool browser does not trust the lab CA. TLS validation was retained; no security warning bypassed. New evidence and access instructions are in `reports/flexiwan/STACK_MIGRATION.md`. This task did not rerun the full packet benchmark, repair S07/S12, or promote the synthetic model to normal live routing.


## 2026-10-02 — balanced three-WAN scenario catalogue

The user approved increasing cases as needed. Added shared balanced-v2 definitions:8 exhaustive binary Up/Down states including CONTROL,15 quality cases,3 flapping,6 ordered mixed impairments and6 telemetry cases =38 selected tests. Original16fault IDs/raw traces remain available through legacy-v1; a frozen pre-change replay SHA-256 passes on both OSes. XGBoost remains a metric forecaster rather than a case classifier.

Simulator/netem/CLI/runners now share definitions and targets. Live saturation uses the correct WAN capacity/namespace/address; telemetry markers specify WAN targets. Both owned guest probe helpers were backed up and atomically updated after identity/source hash verification; Python3.8/pure-function scope fixture checks passed, without injecting live faults. Reports record versioned manifests, use selected-case denominators, separate groups/CONTROL and deny coverage when declared profiles/repeats are missing. Evaluation refuses to overwrite nonempty output.

Windows175unit/behavior tests and Linux183fulltests (including TimescaleDB) passed. Balanced synthetic smoke completed570controller trials at one seed80000; reactive and guarded AI have the same37/37fault handling count. Model fingerprints match the frozen legacy weights; no training/tuning or new packet trial was run. Live API/Grafana remain healthy, Manual/WAN2/MLoff and three fresh WANs. Default initial priorityWAN1 in experiments still limits active-path coverage. New38case live fullgrid is1,368trials/64.6hours minimum; protocol recommends staged runs. See `reports/flexiwan/SCENARIO_DESIGN.md`.

## 2026-10-07 — resume the existing lab

Started the existing AI-SDWAN-Lab distribution, native Docker controller/observability services, both KVM edges and the HTTPS decision API. Current state is Manual / voice / configured priority WAN2, with live ML disabled.

Verified both approved/connected agents, all three tunnels and all six OSPF adjacencies at hello/dead 1/4 seconds. TimescaleDB TLS, Grafana SQL panels, API authorization and LAN ping 3/3 passed. Seven Windows HTTPS observations over at least 60 seconds all had three up/fresh WANs, with advancing raw timestamps.

The first clock-helper attempt returned a nonzero SSH command result; a subsequent identity inspection and clock sync succeeded. The initial strict LAN readback failed; the next direct ping and complete stack verification both passed 3/3. These initial outcomes remain in the tool history and monitor metadata. This was a resume check, with no new fault experiments.

Evidence: [clock](../reports/flexiwan/resume-20261007-161228-clock.json), [OSPF](../reports/flexiwan/resume-20261007-161228-ospf.json), [stack](../reports/flexiwan/resume-20261007-161228-stack.json), [Windows monitor](../reports/flexiwan/resume-20261007-161228-windows-monitor.json).

## 2026-10-08 — enable experimental live ML and inspect machine usage

The user explicitly requested live ML activation. Added --experimental-synthetic-forecast to the live API launcher, including propagation to its background child and the existing CLI. Ruff check/format passed; 26 existing policy/live-sampling tests passed. Deployed the matching launcher to the owned Linux lab and restarted only its decision API.

Verified model_loaded=true, causal_linear_5s sampling, Manual / voice / configured priority WAN2, all three WANs up/fresh and actual predicted metrics for all three WANs over three advancing telemetry frames. The final decision was hold with no pending recommendation. Grafana's ML model active panel returned true. All five Linux model files match the frozen hashes; fingerprint b7b72bfa7c1f34be948b397c4aaebdc639093657c15b444992d8bef3b2bbfc7d. Each XGBoost model reports device=cpu. Activation is experimental; independent real-data validation remains incomplete.

Windows resource sampling at 00:14 Bangkok: CPU average 17.6% (peak 23%), RAM 27.86/31.24 GiB (89.2%), vmmemWSL working set 13.894 GiB, GPU engine counters 0%. C: free 48.03 GiB, E: free 123.95 GiB; both disks 1% busy in the final sample. These are snapshots, not isolated estimates of ML overhead.

Evidence: [live ML](../reports/flexiwan/live-ml-20261008-001001-verification.json), [Grafana/model](../reports/flexiwan/live-ml-20261008-001001-grafana.json), [machine usage](../reports/flexiwan/machine-usage-20261008-001001.json). The launch flag must be supplied to enable ML on a future restart; an unflagged launch retains the disabled default.

## 2026-10-08 — requested Auto + live ML and flow screenshots

Resumed the existing owned lab and enabled Auto with experimental live forecasts. Initial observations had missing predictions and intermittent stale telemetry. Readback found guest clocks ahead of WSL and WSL realtime seven hours ahead of Windows UTC. Stopped only the decision API, disabled NTP in the owned WSL distro and its two identity-verified guests, set WSL realtime from Windows UTC, then force-synchronized guest realtime. Windows time was not modified. Guest clock readback bounds were 0.237/0.221 seconds; freshness and forecast cadence gates were retained.

Added launcher `--stream` propagation to its background child and CLI; default remains `live`. Backed up the previous launcher and deployed matching SHA-256 BDE6F841FBC959CA0FAB9BA2CB30DE3A79B5B57AA3E122582B7396B74E9D1512 to Linux. Ruff check/format, help and AST checks passed. Started stream `live-auto-20261008-162832` with experimental ML, preserving old future-dated stream records, and set `mode=auto` through the authenticated API. Startup still defaults to Manual.

Read-only HTTPS/SQL/controller checks confirmed Auto / voice / configured priority WAN2, model loaded, real forecasts for all three fresh WANs, matching Grafana mode/model panels, both connected approved agents, three configured tunnels and zero unresolved policy intents. A 60-second monitor from 09:46:27–09:47:27 UTC passed all 13 observations. The new stream had zero future telemetry rows. Every observed decision held WAN2 because improvement was below the hysteresis margin; zero new apply events means this is not evidence of a new automatic WAN switch or actual packet-path selection. Real-data model validation remains incomplete.

The initial verification recorded an unsuccessful exact LAN-ping match without retaining raw stdout; its precise packet counts are unknown. That original artifact remains unchanged. The follow-up retained raw ping output and confirmed 3 transmitted / 3 received, with aggregate verification true. No faults were injected or guardrails changed.

Requested native screenshots could not yet be captured because Windows was locked. The computer-use guidance requires stopping UI input until the user unlocks; an unlock request is pending. Backend flow evidence is kept separately and is not labeled as screenshots.

Evidence: [clock repair](../reports/flexiwan/auto-20261008-162832-clock-windows-source.json), [initial verification](../reports/flexiwan/auto-20261008-162832-verification.json), [successful follow-up](../reports/flexiwan/auto-20261008-162832-verification-followup.json), [60-second monitor](../reports/flexiwan/auto-20261008-162832-monitor.json), [flow evidence](../artifacts/screenshots/auto-ml-20261008-162832/flow-evidence.md). Dashboard: `https://localhost:3000/d/ai-sdwan?var-stream=live-auto-20261008-162832`. Recheck owned lab clocks after suspend/restart before starting the decision API.

## 2026-10-09 — resume Auto and experimental live ML

The user requested reopening the stopped lab. Resumed the existing owned WSL distribution, native Docker services, original networks/namespaces and both KVM edges. The hidden keepalive remains running. WSL realtime matched Windows UTC; lab and guest NTP remained disabled. Force-synchronized both identity-verified guest clocks before starting the decision API. The launcher SHA-256 matched the Windows source.

An early OSPF helper call returned a nonzero SSH command result. Read-only inspection subsequently confirmed all three generated tunnel interfaces and full neighbors on both edges. The final timer apply/readback verified hello/dead 1/4 seconds and all six adjacencies. Preflight confirmed both approved/connected agents, matching policies, three fresh healthy probes and zero unresolved intents. Configured priority was already WAN1 before this API activation; the previous recorded October 8 snapshot was WAN2.

Started the API in fresh stream `live-auto-20261009-201515` with the existing experimental forecast flag and unchanged model weights. Waited for actual fresh forecasts on all three WANs, then enabled Auto through the authenticated API. The final read-only HTTPS/SQL/Grafana/controller verification passed, with zero future telemetry rows, ready 13-sample histories, matching Auto/model dashboard panels, all three tunnels and LAN ping 3/3. Observed decisions held WAN1 because the current path remained preferred; the new stream contained zero applied events.

The first LAN verification received 1/3 packets and remains preserved in the initial artifact; a direct follow-up and the final full verification each received 3/3. The initial Windows monitor ended with `httpx.RemoteProtocolError` and did not retain partial observations; its failure is recorded separately, and its cause was not established. The verifier now uses fresh HTTPS connections and records any failed observations. The follow-up passed all 13 observations over 62.06 seconds with Auto, model loaded, three up/fresh WANs, complete predictions and advancing raw timestamps. Windows trusted HTTPS verified API health/docs/authentication plus Grafana login, health and SQL datasource. TLS, telemetry freshness and routing guards were retained.

This was a resume and runtime check. No new fault experiments or real-data model training/validation occurred. Earlier S07/S12 failures and the pending full live benchmark remain unchanged. Configured priority readback does not establish actual packet WAN selection.

Evidence: [clock](../reports/flexiwan/resume-20261009-201515-clock.json), [OSPF](../reports/flexiwan/resume-20261009-201515-ospf-ready.json), [activation](../reports/flexiwan/resume-20261009-201515-activation.json), [initial stack check](../reports/flexiwan/resume-20261009-201515-verification.json), [successful stack follow-up](../reports/flexiwan/resume-20261009-201515-verification-followup.json), [initial Windows monitor failure](../reports/flexiwan/resume-20261009-201515-windows-monitor-initial-error.json), [successful Windows monitor](../reports/flexiwan/resume-20261009-201515-windows-monitor-followup.json). Dashboard: `https://localhost:3000/d/ai-sdwan?var-stream=live-auto-20261009-201515`.

## 2026-10-09 — repair Grafana No data after stream rollover

The user reported No data. API readback showed Auto and experimental ML operating with fresh real telemetry and predictions on all three WANs; the full current-stack check also passed. Grafana's provisioned default was still the archived `live` stream. Reproduced empty Latency, Jitter, Packet loss and WAN utilization queries for that default in the last-hour window, while all 14 SQL panels returned data for the active `live-auto-20261009-201515` stream. The browser inventory exposed no tabs, so the user's actual selected URL was not observed.

Added the default Data stream option `Latest live` (`__latest_live__`). Each panel resolves this option to the most recently updated live service status; named streams continue to resolve directly. The query variable returns the supported PostgreSQL `__text`/`__value` format. Backed up both Windows and Linux source/dashboard files, rebuilt the existing 15-panel dashboard, and deployed it to the owned Grafana provisioning directory. Grafana reloaded it through its existing 15-second polling configuration. No decision API restart or routing configuration change was needed.

Ruff check/format passed for the builder. Verified the live provisioned default, variable query, automatic live resolution, and all 14 SQL panels through Windows trusted HTTPS. Both automatic and explicitly named active selections returned data in every SQL panel, including all four time-series charts; the mode/model panels showed Auto/true. This is API/SQL verification, without a claim of interactive browser QA. Existing archived streams and historical evidence remain available.

Evidence: [current stack](../reports/flexiwan/grafana-no-data-20261009-stack.json), [reproduced default-stream failure](../reports/flexiwan/grafana-no-data-20261009-205358-before.json), [successful automatic/explicit stream checks](../reports/flexiwan/grafana-no-data-20261009-205358-after.json). Current-view link: `https://localhost:3000/d/ai-sdwan?var-stream=__latest_live__&from=now-1h&to=now`. PostgreSQL key/value reference: [Grafana template variables](https://grafana.com/docs/grafana/latest/datasources/postgres/template-variables/).

## 2026-10-09 — fix text status cards and expose Control and approvals

The user still saw NO DATA in Configured priority, Control mode, Application profile and ML model active, and asked about Control and approvals. SQL returned real string values (`wan1`, `auto`, `voice`, `true`); each stat panel's empty `reduceOptions.fields` selected numeric fields only. The same issue affected the string Pending recommendation card. The earlier SQL checks did not validate this display step.

Set stat panel field selection to `/.*/` so both text and numeric fields are included. Moved Control and approvals, Pending recommendation and Decision service heartbeat immediately below the four status cards, shifted the charts/audit down without overlap, and added a Control and approvals link at the dashboard top. The link opens the existing secure Control API for Auto/Manual, profile changes and Approve/Reject. The current mode is Auto and pending recommendation is None; no control mutation was sent during verification.

Backed up both source/dashboard versions and deployed the rebuilt dashboard; provisioned Grafana version advanced from 3 to 4. Ruff check/format passed. Used the actual `@grafana/data` 13.2.3 `getFieldDisplayValues` with stat frames fetched from live Grafana SQL queries: all five string cards reproduced NO DATA with the previous options, then yielded `wan1`, `auto`, `voice`, `true` and `None` with the corrected options. The three numeric WAN health cards still yielded UP. Verified the top control link and near-status card, plus HTTPS 200 Swagger docs with authenticated configuration and approve/reject operations. No interactive browser QA is claimed.

Evidence: [actual Grafana display-function verification](../reports/flexiwan/grafana-stat-20261009-212630-display-verification.json), [all-panel SQL verification](../reports/flexiwan/grafana-stat-20261009-212630-queries.json). The matching library was installed with scripts disabled only in ignored `.work/grafana-stat-20261009-212630`. Field selection reference: [Grafana field display implementation](https://github.com/grafana/grafana/blob/main/packages/grafana-data/src/field/fieldDisplay.ts).

## 2026-10-09 — native Grafana controls and initial GitHub publication

The user requested publishing to `PorametKeawubol/AI_SD-WAN` on `main` and asked for controls inside Grafana. Replaced the Swagger link card with a local native React panel containing Auto/Manual, application profile, and Approve/Reject controls. It reads current service state every five seconds and disables writes for archives, stale/failed state reads, inflight apply or native QBR observation; approval buttons also require an unexpired pending recommendation in Manual mode. Existing decision API approval, expiry, network revalidation and controller readback remain authoritative.

Added a local app plugin with method-specific proxy routes: Viewer can GET state; only Grafana organization Admin can POST configuration or recommendations. The existing API token is provisioned into encrypted `secureJsonData` and injected server-side. Browser session authentication and Grafana CSRF checks remain active. Only the two named local unsigned plugin IDs are enabled; HTML sanitization stays enabled. Grafana now uses native Linux host networking, explicitly binds loopback, verifies API HTTPS with the existing CA, and reaches the existing PostgreSQL loopback mapping under its certificate hostname. SQL datasource remains `sdwan_reader` with verify-full. All existing secrets, certificates, VM/controller resources and model weights were preserved.

Backed up source/configuration and private Grafana environment in the owned Linux `.work/grafana-controls-20261009-215958`, then recreated only Grafana. Decision API was not restarted. Provisioned dashboard advanced to version 5; native app and nested panel modules are registered and served over verified HTTPS. Current API remained Auto / voice / WAN1 with experimental model loaded and no pending/inflight recommendation. An idempotent Admin configuration roundtrip retained mode/profile/priority; no new route approval was attempted.

Validation: all 8 React/DOM behavior tests passed, including protection against a late poll overwriting completed configuration; all 15 existing service/API tests passed; Ruff check/format passed. Real HTTPS verification confirmed encrypted/hidden token, current state and three fresh WANs, API rejection of stale IDs and invalid modes, Viewer read access and rejection of both write operations, anonymous rejection, and cross-origin cookie-session write rejection. The temporary Viewer service account/token was deleted after testing. All 15 SQL panels returned data for both Latest live and the explicit active stream. No interactive browser QA is claimed.

Evidence: [control and authorization checks](../reports/flexiwan/grafana-controls-20261009-220131-verification.json), [all-panel SQL checks](../reports/flexiwan/grafana-controls-20261009-220131-sql-verification.json). References: [Grafana app authentication and encrypted secrets](https://grafana.com/developers/plugin-tools/how-to-guides/app-plugins/add-authentication-for-app-plugins), [nested plugins](https://grafana.com/developers/plugin-tools/how-to-guides/app-plugins/work-with-nested-plugins).

Initialized the previously unversioned workspace on `main` and set the requested `origin`; the remote had no refs. Added Git ignores for node_modules, raw packet captures, legacy SQLite backups and experiment logs; all are preserved locally. Existing ignores exclude private runtime, credentials, virtual environments, vendor and model artifacts. A pre-publication scan of repository candidates against 12 actual private values found no secret matches or literal credential fields; source/results total approximately 33 MiB.
