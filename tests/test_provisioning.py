import copy

import pytest

from scripts.provision_edges import scoped_tunnels
from sdwan.flexiwan import IntegrationError


@pytest.fixture
def tunnel_scope():
    devices = [
        {
            "_id": side,
            "interfaces": [
                {"_id": f"{side}-{wan}", "MAC": f"52:54:00:53:0{site}:0{wan}"}
                for wan in range(1, 4)
            ],
        }
        for site, side in ((1, "a"), (2, "b"))
    ]
    path_labels = {f"wan{i}": f"label-{i}" for i in range(1, 4)}
    labels = {f"label-{i}": {"name": f"WAN{i}"} for i in range(1, 4)}
    tunnel = {
        "deviceA": {"_id": "a"},
        "deviceB": {"_id": "b"},
        "interfaceA": "a-1",
        "interfaceB": "b-1",
        "pathlabel": {"name": "WAN1"},
    }
    return devices, path_labels, labels, tunnel


def test_existing_tunnel_matches_its_exact_vm_interfaces_and_label(tunnel_scope):
    devices, path_labels, labels, tunnel = tunnel_scope
    assert scoped_tunnels([tunnel], devices, path_labels, labels) == {"wan1": tunnel}
    swapped = {
        **tunnel,
        "deviceA": tunnel["deviceB"],
        "deviceB": tunnel["deviceA"],
        "interfaceA": tunnel["interfaceB"],
        "interfaceB": tunnel["interfaceA"],
    }
    assert scoped_tunnels([swapped], devices, path_labels, labels) == {"wan1": swapped}
    reordered = {link: path_labels[link] for link in ("wan3", "wan1", "wan2")}
    assert scoped_tunnels([tunnel], devices, reordered, labels) == {"wan1": tunnel}


@pytest.mark.parametrize("change", ["device", "interface", "label", "duplicate"])
def test_another_device_interface_label_or_duplicate_is_not_reused(tunnel_scope, change):
    devices, path_labels, labels, tunnel = copy.deepcopy(tunnel_scope)
    tunnels = [tunnel]
    if change == "device":
        tunnel["deviceA"]["_id"] = "another-device"
    elif change == "interface":
        tunnel["interfaceA"] = "a-2"
    elif change == "label":
        tunnel["pathlabel"]["name"] = "WAN2"
    else:
        tunnels.append(tunnel)
    with pytest.raises(IntegrationError, match="exact lab WAN scope"):
        scoped_tunnels(tunnels, devices, path_labels, labels)
