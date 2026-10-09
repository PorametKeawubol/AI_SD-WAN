"""Explicit lab identity/timer fixtures; these tests never contact a live edge."""

import copy
import json

import pytest

from lab.edge import ospf


def topology(site=1):
    addresses = [
        {
            "ifname": "eth0",
            "address": f"52:54:00:53:0{site}:00",
            "addr_info": [{"local": f"192.168.56.{10 + site}"}],
        }
    ]
    interfaces = {
        "eth4": {"ipAddress": f"10.{20 if site == 1 else 30}.0.1"},
        **{
            f"vpp{index * 4 + 3}": {
                "ipAddress": f"10.100.0.{index * 2 + site + 1}",
                "ifUp": True,
                "ospfEnabled": True,
                "ipAddressPrefixlen": 31,
                "networkType": "POINTOPOINT",
                "cost": 10,
                "area": "0.0.0.0",
                "routerId": f"192.168.56.{10 + site}",
                "timerMsecs": 10000,
                "timerDeadSecs": 40,
                "nbrAdjacentCount": 1,
            }
            for index in (1, 2, 3)
        },
    }
    return {"interfaces": interfaces}, addresses


def test_timer_scope_follows_verified_addresses_and_preserves_lan():
    document, addresses = topology()
    result = ospf.interfaces_for_site(document, addresses, 1, "sdw-edge-a")
    assert set(result) == {"wan1", "wan2", "wan3"}
    assert result["wan1"]["interface"] == "vpp7"
    assert all(row["interface"] != "eth4" for row in result.values())
    addresses[0]["address"] = "52:54:00:00:00:00"
    with pytest.raises(ValueError, match="Management NIC"):
        ospf.interfaces_for_site(document, addresses, 1, "sdw-edge-a")


def test_mismatched_tunnel_cost_is_rejected_before_timer_mutation():
    document, addresses = topology()
    document["interfaces"]["vpp11"]["cost"] = 20
    with pytest.raises(ValueError, match="topology"):
        ospf.interfaces_for_site(document, addresses, 1, "sdw-edge-a")


def test_timer_backup_idempotency_and_restoration(tmp_path, monkeypatch):
    document, addresses = topology()
    actual = ospf.interfaces_for_site(document, addresses, 1, "sdw-edge-a")
    mutations = []
    monkeypatch.setattr(ospf, "inspect", lambda site: copy.deepcopy(actual))

    def mutate(arguments):
        mutations.append(arguments)
        desired = ospf.DEFAULT if "ip ospf hello-interval 10" in arguments else ospf.TUNED
        for row in actual.values():
            row.update(desired)
        return "Configuration saved"

    monkeypatch.setattr(ospf, "cli", mutate)
    backup = tmp_path / "backup.json"
    assert ospf.configure(1, apply=True, backup=backup)["changed"]
    saved = json.loads(backup.read_text())
    assert saved["interfaces"]["wan1"]["dead_seconds"] == 40
    assert not ospf.configure(1, apply=True, backup=backup)["changed"]
    assert len(mutations) == 1
    assert ospf.configure(1, restore=True, backup=backup)["changed"]
    assert actual["wan1"]["dead_seconds"] == 40
    assert json.loads(backup.read_text()) == saved
    assert not any("eth4" in command for mutation in mutations for command in mutation)


def test_unknown_existing_timers_are_preserved(tmp_path, monkeypatch):
    document, addresses = topology()
    actual = ospf.interfaces_for_site(document, addresses, 1, "sdw-edge-a")
    actual["wan2"]["dead_seconds"] = 20
    monkeypatch.setattr(ospf, "inspect", lambda site: actual)
    monkeypatch.setattr(
        ospf, "cli", lambda command: pytest.fail("Mutation was not authorized by identity")
    )
    backup = tmp_path / "backup.json"
    with pytest.raises(ValueError, match="Unknown existing timers"):
        ospf.configure(1, apply=True, backup=backup)
    assert not backup.exists()
