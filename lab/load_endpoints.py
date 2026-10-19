"""Temporary receiver namespaces sharing the owned WAN bottlenecks.

These endpoints represent traffic elsewhere on an ISP link. Their packets
cross the WAN namespace qdisc but do not enter flexiEdge: VPP utilization
counters therefore do not include this external background traffic.
"""

import json
import subprocess
from pathlib import Path

from lab.impairments import require_linux
from lab.linux_topology import network_matches, network_xml
from lab.resume import output, verified_namespaces

ROOT = Path(__file__).resolve().parents[1]


def endpoint_plan():
    return [
        {
            "link": f"wan{index}", "side": side,
            "namespace": f"sdw-load{index}{side}",
            "outside": f"sdw-l{index}{side}-v", "bridge": f"sdw-w{index}{side}",
            "address": f"172.16.{index}.{101 if side == 'a' else 102}",
            "port": 5310 + index,
        }
        for index in (1, 2, 3) for side in ("a", "b")
    ]


def _run(*arguments):
    subprocess.run(list(arguments), check=True, timeout=15)


def _namespace_identity(namespace):
    stat = Path("/run/netns", namespace).stat()
    return [stat.st_dev, stat.st_ino]


def _links(namespace=None):
    scope = ["ip", "-n", namespace] if namespace else ["ip"]
    return {row["ifname"]: row for row in json.loads(output(*scope, "-json", "-details", "link", "show"))}


def _owned_host():
    require_linux()
    if not (ROOT / ".ai-sdwan-owned").is_file():
        raise RuntimeError("Use the prepared isolated Linux lab")


def _preflight():
    _owned_host()
    if not verified_namespaces():
        raise RuntimeError("The complete owned WAN topology is required")
    existing = {row.split()[0] for row in output("ip", "netns", "list").splitlines()}
    host = _links()
    planned = endpoint_plan()
    # Check every target before the first mutation; never reuse existing names.
    for binding in planned:
        if binding["namespace"] in existing or binding["outside"] in host:
            raise RuntimeError("An endpoint name already exists; existing resources preserved")
        bridge = host.get(binding["bridge"], {})
        index, side = binding["link"][-1], binding["side"]
        xml = output("virsh", "net-dumpxml", f"sdw-wan{index}{side}")
        if (
            bridge.get("linkinfo", {}).get("info_kind") != "bridge"
            or not network_matches(xml, network_xml(f"sdw-wan{index}{side}", binding["bridge"]))
        ):
            raise RuntimeError("WAN bridge differs from the owned lab; preserved")
        binding["bridge_ifindex"] = bridge["ifindex"]
    return planned


def _clear_endpoint_neighbors(created):
    """Forget only these receiver IPs on the verified central WAN bridge.

    The clients bind .254 on br0; a/b are bridge ports, not their L3 neighbor
    interface. Recreated receivers have new MACs, so retain no old ARP entry.
    Guest .1/.2 and every other neighbor remain untouched.
    """
    planned = {row["namespace"]: row for row in endpoint_plan()}
    for binding in created:
        expected = planned.get(binding.get("namespace"))
        if expected is None or any(binding.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Neighbor target differs from the scoped endpoint plan; preserved")
    if not verified_namespaces():
        raise RuntimeError("Owned WAN topology is unavailable; neighbors preserved")
    for binding in created:
        _run("ip", "-n", "sdw-" + binding["link"], "neigh", "flush", "to",
             binding["address"] + "/32", "dev", "br0")


def create_endpoints():
    """Return ownership records; caller must stop servers before removing them."""
    planned = _preflight()
    created = []
    try:
        for binding in planned:
            namespace, outside = binding["namespace"], binding["outside"]
            _run("ip", "netns", "add", namespace)
            binding.update(namespace_identity=_namespace_identity(namespace),
                           host_ifindex=None, peer_ifindex=None, attached=False, addressed=False)
            created.append(binding)
            _run("ip", "link", "add", outside, "type", "veth", "peer", "name", "eth0", "netns", namespace)
            host, peer = _links()[outside], _links(namespace)["eth0"]
            binding.update(host_ifindex=host["ifindex"], peer_ifindex=peer["ifindex"])
            _run("ip", "link", "set", outside, "master", binding["bridge"])
            binding["attached"] = True
            _run("ip", "link", "set", outside, "up")
            _run("ip", "-n", namespace, "addr", "add", binding["address"] + "/24", "dev", "eth0")
            binding["addressed"] = True
            _run("ip", "-n", namespace, "link", "set", "eth0", "up")
            _run("ip", "-n", namespace, "link", "set", "lo", "up")
        _clear_endpoint_neighbors(created)
        return created
    except Exception:
        try:
            remove_endpoints(created)
        except Exception as cleanup_error:
            raise RuntimeError("Endpoint creation failed and owned cleanup needs inspection") from cleanup_error
        raise


def _verify_record(binding, host):
    planned = {row["namespace"]: row for row in endpoint_plan()}
    expected = planned.get(binding.get("namespace"))
    if expected is None or any(binding.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Endpoint record differs from the scoped plan; preserved")
    namespace = binding["namespace"]
    if _namespace_identity(namespace) != binding["namespace_identity"]:
        raise RuntimeError("Endpoint namespace identity changed; preserved")
    inside = _links(namespace)
    bridge = host.get(binding["bridge"], {})
    if bridge.get("ifindex") != binding["bridge_ifindex"] or bridge.get("linkinfo", {}).get("info_kind") != "bridge":
        raise RuntimeError("Endpoint bridge identity changed; preserved")
    outside = host.get(binding["outside"])
    if binding["host_ifindex"] is None:
        if outside is not None or set(inside) != {"lo"}:
            raise RuntimeError("Partially created endpoint has unknown links; preserved")
        return
    peer = inside.get("eth0", {})
    if (
        set(inside) != {"lo", "eth0"}
        or outside is None
        or outside.get("ifindex") != binding["host_ifindex"]
        or peer.get("ifindex") != binding["peer_ifindex"]
        or outside.get("link_index") != peer.get("ifindex")
        or peer.get("link_index") != outside.get("ifindex")
        or outside.get("linkinfo", {}).get("info_kind") != "veth"
        or peer.get("linkinfo", {}).get("info_kind") != "veth"
        or outside.get("master") != (binding["bridge"] if binding["attached"] else None)
    ):
        raise RuntimeError("Endpoint veth identity or bridge changed; preserved")
    rows = json.loads(output("ip", "-n", namespace, "-json", "address", "show", "dev", "eth0"))
    addresses = {(entry["local"], entry["prefixlen"]) for row in rows
                 for entry in row.get("addr_info", []) if entry["family"] == "inet"}
    if addresses != ({(binding["address"], 24)} if binding["addressed"] else set()):
        raise RuntimeError("Endpoint address changed; preserved")


def remove_endpoints(created):
    """Delete only unchanged namespaces/veth pairs owned by this creation call."""
    if not created:
        return
    _owned_host()
    host = _links()
    if len({row.get("namespace") for row in created}) != len(created):
        raise RuntimeError("Duplicate endpoint ownership records; preserved")
    # Validate every resource first so a conflict does not cause partial deletion.
    for binding in created:
        _verify_record(binding, host)
    _clear_endpoint_neighbors(created)
    for binding in reversed(created):
        if binding["host_ifindex"] is not None:
            _run("ip", "link", "delete", binding["outside"])
        _run("ip", "netns", "delete", binding["namespace"])
