"""Exercise pinned API contract against the real isolated controller, with no edge."""

import copy
import json
import os
import ssl
import time
from pathlib import Path

import httpx

from lab.baselines import ensure_application, experiment_policy
from sdwan.flexiwan import FlexiManageClient, FlexiWANConfig, policy_body, policy_signature

ROOT = Path(__file__).resolve().parents[1]


def main():
    credentials = json.loads((ROOT / ".work/native/credentials.json").read_text(encoding="utf-8"))
    os.environ["FLEXIWAN_ACCESS_KEY"] = credentials["access_key"]
    configuration = FlexiWANConfig.model_validate(
        {
            "base_url": "https://127.0.0.1:3443/api",
            "organization_id": credentials["organization_id"],
            "device_ids": ["000000000000000000000001"],
            "policy_id": credentials["policy_id"],
            "rule_id": credentials["rule_id"],
            "ca_file": credentials["ca_file"],
            "links": {
                link: {"path_label_id": identifier, "capacity_mbps": capacity}
                for (link, identifier), capacity in zip(
                    credentials["path_labels"].items(), (100, 80, 30)
                )
            },
        }
    )
    client = FlexiManageClient(configuration)
    client.verify_version()
    assert not client.devices(), "Stop: controller now has devices; use the real edge verifier"
    original = policy_body(client.policy())
    snapshot = ROOT / ".work/native/policy-before-api-verification.json"
    snapshot.write_text(json.dumps(original, indent=2), encoding="utf-8")
    changed = copy.deepcopy(original)
    rule = client.managed_rule(changed)
    rule["action"]["links"][0], rule["action"]["links"][1] = (
        rule["action"]["links"][1],
        rule["action"]["links"][0],
    )
    timings = []
    try:
        start = time.perf_counter()
        submitted = client.request("PUT", f"mlpolicies/{configuration.policy_id}", body=changed)
        timings.append(time.perf_counter() - start)
        assert submitted["status"] == "completed" and submitted["ids"] == []
        assert client.current_path(client.policy()) == "wan2"
        assert client.managed_rule(client.policy())["_id"] == credentials["rule_id"]
        for profile in ("voice", "interactive", "bulk"):
            application_id = ensure_application(client, profile)
            assert ensure_application(client, profile) == application_id
            qbr = experiment_policy(client, original, profile, "native_qbr", application_id)
            client.request("PUT", f"mlpolicies/{configuration.policy_id}", body=qbr)
            assert client.current_path(client.policy()) is None
            assert policy_signature(client.policy()) == policy_signature(qbr)
    finally:
        client.request("PUT", f"mlpolicies/{configuration.policy_id}", body=original)
        assert policy_signature(client.policy()) == policy_signature(original)
    try:
        client.preflight()
    except RuntimeError:
        no_edge_refused = True
    else:
        raise AssertionError("Preflight accepted a missing edge")
    with httpx.Client(
        verify=ssl.create_default_context(cafile=configuration.ca_file), trust_env=False
    ) as http:
        denied = http.get(
            configuration.base_url + "/devices", headers={"Authorization": "Bearer invalid-key"}
        )
        assert denied.status_code == 401
    report = {
        "evidence_level": "real_controller_api",
        "controller_version": "6.3.37",
        "timestamp": time.time(),
        "openapi_contract_verified": True,
        "policy_put_get_restore": True,
        "rule_id_preserved": True,
        "custom_application_classifiers": 3,
        "native_qbr_policy_put_get_restore": True,
        "invalid_access_key_rejected": True,
        "no_edge_preflight_refused": no_edge_refused,
        "put_duration_seconds": timings,
        "real_edge": False,
        "packet_forwarding_tested": False,
    }
    path = ROOT / "reports/flexiwan/api-verification.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    client.close()


if __name__ == "__main__":
    main()
