"""Endpoint lifecycle fixtures exercise ownership checks without host mutations."""

import copy
import json

import pytest

from lab import load_endpoints
from lab.linux_topology import network_xml


def topology(tmp_path, monkeypatch, *, fail=None):
    (tmp_path / ".ai-sdwan-owned").touch()
    monkeypatch.setattr(load_endpoints, "ROOT", tmp_path)
    monkeypatch.setattr(load_endpoints, "require_linux", lambda: None)
    monkeypatch.setattr(load_endpoints, "verified_namespaces", lambda: True)
    host, namespaces, calls, queries = {}, {}, [], []
    for index, binding in enumerate(load_endpoints.endpoint_plan(), start=1):
        host[binding["bridge"]] = {"ifname": binding["bridge"], "ifindex": index,
                                   "linkinfo": {"info_kind": "bridge"}}

    def links(namespace=None):
        return copy.deepcopy(namespaces[namespace]["links"] if namespace else host)

    def identity(namespace):
        return copy.deepcopy(namespaces[namespace]["identity"])

    def output(*arguments):
        queries.append(arguments)
        if arguments == ("ip", "netns", "list"):
            return "\n".join(["sdw-wan1", "sdw-wan2", "sdw-wan3", "sdw-client", "sdw-server", *namespaces])
        if arguments[:2] == ("virsh", "net-dumpxml"):
            name = arguments[2]
            return network_xml(name, "sdw-w" + name.removeprefix("sdw-wan"))
        if "address" in arguments:
            addresses = namespaces[arguments[2]]["addresses"]
            return json.dumps([{"addr_info": [{"family": "inet", "local": address,
                                                "prefixlen": 24} for address in addresses]}])
        raise AssertionError(arguments)

    def run(*arguments):
        calls.append(arguments)
        if fail and fail(arguments):
            raise RuntimeError("fixture creation failed")
        if arguments[:3] == ("ip", "netns", "add"):
            namespace = arguments[3]
            namespaces[namespace] = {"identity": [99, 1000 + len(namespaces)],
                                     "links": {"lo": {"ifname": "lo", "ifindex": 1}},
                                     "addresses": []}
        elif arguments[:3] == ("ip", "link", "add"):
            outside, namespace = arguments[3], arguments[-1]
            index = 100 + len(host)
            peer = index + 100
            host[outside] = {"ifname": outside, "ifindex": index, "link_index": peer,
                             "linkinfo": {"info_kind": "veth"}}
            namespaces[namespace]["links"]["eth0"] = {
                "ifname": "eth0", "ifindex": peer, "link_index": index,
                "linkinfo": {"info_kind": "veth"}}
        elif arguments[:3] == ("ip", "link", "set") and "master" in arguments:
            host[arguments[3]]["master"] = arguments[-1]
        elif arguments[:2] == ("ip", "-n") and "addr" in arguments:
            namespaces[arguments[2]]["addresses"].append(arguments[5].split("/")[0])
        elif arguments[:3] == ("ip", "link", "delete"):
            outside = host.pop(arguments[3])
            for state in namespaces.values():
                peer = state["links"].get("eth0")
                if peer and peer["ifindex"] == outside["link_index"]:
                    del state["links"]["eth0"]
        elif arguments[:3] == ("ip", "netns", "delete"):
            del namespaces[arguments[3]]

    monkeypatch.setattr(load_endpoints, "_links", links)
    monkeypatch.setattr(load_endpoints, "_namespace_identity", identity)
    monkeypatch.setattr(load_endpoints, "output", output)
    monkeypatch.setattr(load_endpoints, "_run", run)
    return host, namespaces, calls, queries


def test_endpoint_creation_and_removal_preserves_wan_bridges(tmp_path, monkeypatch):
    host, namespaces, calls, queries = topology(tmp_path, monkeypatch)
    baseline_bridges = copy.deepcopy(host)
    created = load_endpoints.create_endpoints()
    assert len(created) == len(namespaces) == 6
    assert len([query for query in queries if query[0] == "virsh"]) == 6
    for binding in created:
        assert binding["address"].endswith(".101" if binding["side"] == "a" else ".102")
        assert host[binding["outside"]]["master"] == binding["bridge"]
        assert namespaces[binding["namespace"]]["addresses"] == [binding["address"]]
    load_endpoints.remove_endpoints(created)
    assert namespaces == {} and host == baseline_bridges
    assert all(call[3].startswith("sdw-load") for call in calls if call[:3] == ("ip", "netns", "delete"))


def test_last_endpoint_name_conflict_prevents_every_mutation(tmp_path, monkeypatch):
    host, _, calls, _ = topology(tmp_path, monkeypatch)
    host["sdw-l3b-v"] = {"ifname": "sdw-l3b-v", "ifindex": 900}
    with pytest.raises(RuntimeError, match="already exists"):
        load_endpoints.create_endpoints()
    assert calls == []


def test_unknown_wan_bridge_is_preserved_before_mutation(tmp_path, monkeypatch):
    host, _, calls, _ = topology(tmp_path, monkeypatch)
    host["sdw-w3b"]["linkinfo"]["info_kind"] = "veth"
    with pytest.raises(RuntimeError, match="bridge differs"):
        load_endpoints.create_endpoints()
    assert calls == []


def test_partial_creation_failure_removes_only_created_namespaces(tmp_path, monkeypatch):
    host, namespaces, calls, _ = topology(tmp_path, monkeypatch, fail=lambda command:
        command == ("ip", "-n", "sdw-load2a", "link", "set", "eth0", "up"))
    baseline_bridges = copy.deepcopy(host)
    with pytest.raises(RuntimeError, match="fixture creation failed"):
        load_endpoints.create_endpoints()
    assert namespaces == {} and host == baseline_bridges
    removed = [call[3] for call in calls if call[:3] == ("ip", "netns", "delete")]
    assert set(removed) == {"sdw-load1a", "sdw-load1b", "sdw-load2a"}


def test_failure_before_veth_creation_still_cleans_owned_empty_namespace(tmp_path, monkeypatch):
    host, namespaces, _, _ = topology(tmp_path, monkeypatch,
        fail=lambda command: command[:3] == ("ip", "link", "add"))
    baseline = copy.deepcopy(host)
    with pytest.raises(RuntimeError, match="fixture creation failed"):
        load_endpoints.create_endpoints()
    assert namespaces == {} and host == baseline


@pytest.mark.parametrize("conflict", ["namespace", "peer", "bridge", "address", "extra_link"])
def test_concurrent_resource_change_preserves_all_endpoints(tmp_path, monkeypatch, conflict):
    host, namespaces, calls, _ = topology(tmp_path, monkeypatch)
    created = load_endpoints.create_endpoints()
    binding = created[-1]
    state = namespaces[binding["namespace"]]
    if conflict == "namespace":
        state["identity"][1] += 1
    elif conflict == "peer":
        state["links"]["eth0"]["link_index"] += 1
    elif conflict == "bridge":
        host[binding["outside"]]["master"] = "production"
    elif conflict == "address":
        state["addresses"].append("192.0.2.1")
    else:
        state["links"]["other"] = {"ifname": "other"}
    calls.clear()
    with pytest.raises(RuntimeError, match="changed|unknown|identity"):
        load_endpoints.remove_endpoints(created)
    assert calls == [] and len(namespaces) == 6


def test_modified_or_duplicate_ownership_records_do_not_delete_resources(tmp_path, monkeypatch):
    _, namespaces, calls, _ = topology(tmp_path, monkeypatch)
    created = load_endpoints.create_endpoints()
    calls.clear()
    with pytest.raises(RuntimeError, match="Duplicate"):
        load_endpoints.remove_endpoints([created[0], created[0]])
    changed = copy.deepcopy(created)
    changed[0]["namespace"] = "production"
    with pytest.raises(RuntimeError, match="scoped plan"):
        load_endpoints.remove_endpoints(changed)
    assert calls == [] and len(namespaces) == 6


def test_recreated_receivers_clear_only_exact_endpoint_neighbors_on_the_owned_client_bridge(tmp_path, monkeypatch):
    _, _, calls, _ = topology(tmp_path, monkeypatch)
    created = load_endpoints.create_endpoints()
    expected = {
        ("ip", "-n", f"sdw-wan{index}", "neigh", "flush", "to",
         f"172.16.{index}.{site}/32", "dev", "br0")
        for index in (1, 2, 3) for site in (101, 102)
    }
    flushed = [call for call in calls if "neigh" in call]
    assert len(flushed) == 6 and set(flushed) == expected
    calls.clear()
    load_endpoints.remove_endpoints(created)
    flushed = [call for call in calls if "neigh" in call]
    assert len(flushed) == 6 and set(flushed) == expected
    first_delete = next(index for index, call in enumerate(calls) if "delete" in call)
    assert all("neigh" in call for call in calls[:first_delete])


def test_arbitrary_neighbor_target_is_rejected_before_any_cache_change(tmp_path, monkeypatch):
    _, _, calls, _ = topology(tmp_path, monkeypatch)
    planned = load_endpoints.endpoint_plan()
    planned[-1]["address"] = "172.16.3.2"  # The guest itself must never be flushed.
    with pytest.raises(RuntimeError, match="scoped endpoint plan"):
        load_endpoints._clear_endpoint_neighbors(planned)
    assert calls == []
    planned = load_endpoints.endpoint_plan()
    planned[-1]["link"] = "production"
    with pytest.raises(RuntimeError, match="scoped endpoint plan"):
        load_endpoints._clear_endpoint_neighbors(planned)
    assert calls == []


def test_missing_owned_wan_topology_preserves_neighbors_and_endpoints(tmp_path, monkeypatch):
    _, namespaces, calls, _ = topology(tmp_path, monkeypatch)
    created = load_endpoints.create_endpoints()
    calls.clear()
    monkeypatch.setattr(load_endpoints, "verified_namespaces", lambda: False)
    with pytest.raises(RuntimeError, match="neighbors preserved"):
        load_endpoints.remove_endpoints(created)
    assert calls == [] and len(namespaces) == 6
