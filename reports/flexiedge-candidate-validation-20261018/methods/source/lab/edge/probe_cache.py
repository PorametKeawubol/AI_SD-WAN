#!/usr/bin/env python3
"""Run the unchanged measured probe on the edge; RPC reads its latest result.

Compatible with the pinned edge's Python 3.8. Never retimes, fabricates, fills
or extrapolates measurements. Stale/absent results fail closed.
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

try:
    from .probe import measure
except ImportError:  # Installed next to probe.py on the owned flexiEdge.
    from probe import measure

CACHE = Path('/run/ai-sdwan/probe-cache.json')


def read_cached(path=CACHE, now=None):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    now = time.time() if now is None else now
    if payload.get('schema') != 1 or payload.get('measurement') != 'icmp_rtt_stddev':
        raise ValueError('Unexpected cached measurement contract')
    if payload.get('probe_packets_per_wan') != 100:
        raise ValueError('Cached measurement must retain the 100-packet probe')
    samples = payload.get('samples')
    if not isinstance(samples, list) or not samples:
        raise ValueError('No cached measured WAN sample')
    if any(not -1 <= now - row['timestamp'] <= 10 for row in samples):
        raise ValueError('Cached measurement stale or from an invalid future clock')
    return payload


def collect_once(config, path=CACHE):
    # measure() timestamps the actual completed ICMP/VPP measurement. If it
    # fails, the old file keeps its old timestamp and naturally ages out.
    payload = measure(config)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(path)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read', action='store_true')
    parser.add_argument('--config', default='/etc/ai-sdwan/probe.json')
    args = parser.parse_args()
    if args.read:
        print(json.dumps(read_cached(), allow_nan=False))
        return
    if os.geteuid() != 0 or socket.gethostname() not in ('sdw-edge-a', 'sdw-edge-b'):
        raise RuntimeError('Continuous sampling is restricted to the owned lab edges')
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    due = time.monotonic()
    while not stopping:
        time.sleep(max(0, due - time.monotonic()))
        if stopping:
            break
        try:
            collect_once(config)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            print('Measurement unavailable: ' + type(error).__name__, file=sys.stderr, flush=True)
        due += 5
        while due < time.monotonic():
            due += 5  # Preserve missed slots as gaps; never manufacture catch-up samples.


if __name__ == '__main__':
    main()
