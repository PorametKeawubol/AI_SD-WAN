"""Read-only clock verification with native guest commands and bounded RTT."""

import argparse
import json
import subprocess
import time
from pathlib import Path

from lab.edge.clock import verify_identity

ROOT = Path(__file__).resolve().parents[1]


def remote(options, target, command):
    return subprocess.run(['ssh', *options, target, command], capture_output=True,
                          text=True, timeout=12, check=True).stdout


def observe(site, options):
    target = f'lab@192.168.56.{10 + site}'
    hostname = remote(options, target, '/bin/hostname').strip()
    addresses = json.loads(remote(options, target, '/sbin/ip -json address show dev eth0'))
    verify_identity(site, hostname, addresses)
    for attempt in range(1, 4):
        before = time.time()
        guest = float(remote(options, target, '/bin/date +%s.%N').strip())
        after = time.time()
        # A native date process avoids measuring Python interpreter teardown.
        # Both bounds must fit, so transport delay cannot masquerade as skew.
        bounds = [guest - after, guest - before]
        if -1 <= bounds[0] <= bounds[1] <= 1:
            return {'site': site, 'changed': False, 'guest_before_epoch': guest,
                    'guest_after_epoch': guest, 'host_request_epoch': before,
                    'after_offset_upper_bound_seconds': after - guest,
                    'clock_offset_bounds_seconds': bounds,
                    'round_trip_seconds': after - before, 'verification_attempt': attempt}
    raise RuntimeError(f'Guest clock or transport uncertainty exceeds one second: site {site}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    assert (ROOT / '.ai-sdwan-owned').is_file(), 'Use the owned Linux lab'
    if args.output.exists():
        raise FileExistsError('Preserve earlier clock verification')
    options = ['-i', str(ROOT / 'lab/runtime/edge-key'), '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o',
               'UserKnownHostsFile=' + str(ROOT / 'lab/runtime/known_hosts'),
               '-o', 'ConnectTimeout=5']
    rows = [observe(site, options) for site in (1, 2)]
    result = {'observed_at': time.time(), 'evidence_level': 'real_edge_clock_observations',
              'applied': False, 'source': 'Native guest UTC clock with verified identity and bounded SSH RTT',
              'edges': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
