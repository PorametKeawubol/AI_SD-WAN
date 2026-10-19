"""Restore only this task's temporary policy, sampler, guests and VM definitions."""
from pathlib import Path
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT=Path('/opt/ai-sdwan-lab')
WORK=Path('/mnt/e/Proposal/.codex-review/loss-sla-20261018')
sys.path.insert(0,str(ROOT))
from lab.impairments import apply, plan
from lab.resume import vm_matches
from sdwan.adapters import FlexiWANAdapter
from sdwan.flexiwan import FlexiWANConfig, policy_signature

assert (ROOT/'.ai-sdwan-owned').is_file()
report={'scope':'Only temporary resources created for this task; no host reboot or update removal',
        'started_at':time.time()}
def save():
    (WORK/'lab-restoration.json').write_text(json.dumps(report,indent=2)+'\n')
def run(*args,timeout=30):
    return subprocess.run(args,capture_output=True,text=True,check=True,timeout=timeout).stdout

for port in [8000,8001]:
    with socket.socket() as sock:
        assert sock.connect_ex(('127.0.0.1',port))!=0, 'Experiment API is still active'
for path in (ROOT/'reports/loss-sla-tcg-voice-20261018-retry1').glob('*/trial.json'):
    assert json.loads(path.read_text())['status']!='running', 'An owned trial is still running'

os.environ['FLEXIWAN_ACCESS_KEY']=json.loads((ROOT/'lab/runtime/credentials.json').read_text())['access_key']
config=FlexiWANConfig.load(ROOT/'config/flexiwan.tcg-cache-20261018.local.json')
adapter=FlexiWANAdapter(config)
try:
    pending=adapter.journal.unresolved()
    if pending:
        assert len(pending)==1
        entry=json.loads(pending[0].read_text())
        remote,devices=adapter.client.preflight(reconciling=True)
        assert policy_signature(remote)==policy_signature(entry['requested'])
        assert all(job['state']=='complete' for job in adapter.client.jobs(entry['job_ids']))
        adapter.client.readback(remote)
        adapter.reconcile()
        report['completed_write_reconciled_without_replay']=pending[0].name
        save()
    source=ROOT/'reports/tcg-control-readiness-20261018-4/CONTROL-voice-reactive-000/baseline-policy.json'
    original=json.loads(source.read_text())['original']
    snapshot=WORK/'original-pre-sla-policy.json'
    if not snapshot.exists():
        snapshot.write_text(json.dumps(original,indent=2)+'\n')
    remote,devices=adapter.client.preflight()
    adapter.client.readback(remote)
    if policy_signature(remote)!=policy_signature(original):
        adapter.apply_experiment_policy(original,'task-restore-'+uuid.uuid4().hex)
    adapter.client.readback(original)
    assert not adapter.journal.unresolved()
    report['policy_restored_to_first_trial_original']=True
    report['both_agents_verified']=True
    report['unresolved_journals']=0
    save()
finally:
    adapter.client.close()

apply(plan('CONTROL'))
report['netem_restored_to_control']=True
options=['-i',str(ROOT/'lab/runtime/edge-key'),'-o','UserKnownHostsFile='+str(ROOT/'lab/runtime/known_hosts'),
         '-o','StrictHostKeyChecking=yes','-o','BatchMode=yes','-o','ConnectTimeout=8',
         '-oControlPath='+str(ROOT/'lab/runtime/ssh-mux-20261018/%h-%p-%r')]
guest_code="""import json,socket,subprocess
assert socket.gethostname()=='sdw-edge-a'
subprocess.run(['systemctl','stop','ai-sdwan-probe-cache'],check=True)
active=subprocess.run(['systemctl','is-active','ai-sdwan-probe-cache'],capture_output=True,text=True)
enabled=subprocess.run(['systemctl','is-enabled','ai-sdwan-probe-cache'],capture_output=True,text=True)
assert active.stdout.strip()=='inactive' and enabled.stdout.strip() in ('disabled','static')
print(json.dumps({'sampler_active':False,'sampler_enabled_at_reboot':False}))
"""
stopped=subprocess.run(['ssh',*options,'lab@192.168.56.11','sudo python3 -'],
                       input=guest_code,text=True,capture_output=True,check=True,timeout=45)
report['sampler']=json.loads(stopped.stdout)
save()
run(sys.executable,str(WORK/'ssh_sessions.py'),'--stop')
report['owned_ssh_masters_stopped']=True
save()

backups={}
for side in ['a','b']:
    name='sdw-edge-'+side
    backup=WORK/(name+'-original.xml')
    content=backup.read_bytes()
    node=ET.fromstring(content)
    assert vm_matches(content.decode(),side) and node.get('type')=='kvm'
    assert node.findtext('vcpu')=='2' and node.findtext('memory')=='4194304'
    current=run('virsh','dumpxml',name)
    assert vm_matches(current,side)
    assert ET.fromstring(current).findtext('uuid')==node.findtext('uuid')
    backups[name]=content
    state=run('virsh','domstate',name).strip()
    if state=='running':
        run('virsh','shutdown',name)
    else:
        assert state=='shut off'
report['guests_shutdown_requested']=True
save()
deadline=time.monotonic()+180
while True:
    states={name:run('virsh','domstate',name).strip() for name in backups}
    report['guest_states']=states
    save()
    print('Guest shutdown: '+json.dumps(states),flush=True)
    if all(value=='shut off' for value in states.values()):
        break
    assert time.monotonic()<deadline, 'Graceful shutdown pending; do not force-destroy guests'
    time.sleep(10)

# libvirt cannot define KVM while ENODEV persists. Restore exact saved inactive
# files with its daemon stopped, then let it reload them for the user's restart.
run('systemctl','stop','libvirtd.service')
try:
    for name,content in backups.items():
        target=Path('/etc/libvirt/qemu')/(name+'.xml')
        assert target.resolve().parent==Path('/etc/libvirt/qemu')
        assert target.exists()
        temporary=target.with_suffix('.task-restore.tmp')
        temporary.write_bytes(content)
        temporary.chmod(0o600)
        temporary.replace(target)
        assert target.read_bytes()==content
finally:
    run('systemctl','start','libvirtd.service')
report['original_kvm_definitions_restored']=True
report['definitions']={}
for side in ['a','b']:
    name='sdw-edge-'+side
    path=Path('/etc/libvirt/qemu')/(name+'.xml')
    assert path.read_bytes()==backups[name]
    result=subprocess.run(['virsh','dumpxml',name,'--inactive'],capture_output=True,text=True,timeout=30)
    observed=ET.fromstring(result.stdout) if result.returncode==0 else None
    report['definitions'][name]={'disk_xml_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'exact_backup_restored':True,'libvirt_loaded':observed is not None,
        'type':observed.get('type') if observed is not None else 'kvm (saved definition)',
        'vcpu':observed.findtext('vcpu') if observed is not None else '2',
        'state':run('virsh','domstate',name).strip() if observed is not None else 'inactive definition; reload after KVM recovery'}
    if observed is not None:
        assert observed.get('type')=='kvm' and observed.findtext('vcpu')=='2'
try:
    fd=os.open('/dev/kvm',os.O_RDWR)
except OSError as error:
    report['kvm']={'opens':False,'errno':error.errno}
else:
    os.close(fd)
    report['kvm']={'opens':True}
report['complete']=True
report['completed_at']=time.time()
save()
print(json.dumps(report,indent=2),flush=True)
