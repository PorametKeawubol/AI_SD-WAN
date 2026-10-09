"""Identical explicit UDP classifiers for native priority, native QBR and AI trials."""

from sdwan.flexiwan import IntegrationError, policy_body

PROFILE_PORT = {"voice": 5209, "interactive": 5210, "bulk": 5211}
SERVICE_CLASS = {"voice": "real-time", "interactive": "low-latency", "bulk": "high-throughput"}


def ensure_application(client, profile):
    name = "custom:ai-lab-" + profile
    expected = {
        "name": name,
        "description": "Explicit UDP echo classifier for isolated AI SD-WAN evaluation",
        "category": "ai-sdwan-lab",
        "serviceClass": SERVICE_CLASS[profile],
        "importance": "high",
        "rules": [{"ip": "", "ports": str(PROFILE_PORT[profile]), "protocol": "udp"}],
    }
    matches = [
        row
        for row in client.request("GET", "appidentifications")["appIdentifications"]
        if row["name"] == name
    ]
    if len(matches) > 1:
        raise IntegrationError("Ambiguous lab application classifier")
    if matches:
        result = client.request("GET", "appidentifications/custom/" + matches[0]["id"])
    else:
        result = client.request("POST", "appidentifications", body=expected)
    actual = {key: result.get(key) for key in expected}
    actual["rules"] = [
        {key: rule.get(key, "") for key in ("ip", "ports", "protocol")}
        for rule in result.get("rules", [])
    ]
    if actual != expected:
        raise IntegrationError("Existing lab classifier differs; inspect instead of overwriting")
    return result["id"]


def experiment_policy(client, original, profile, strategy, application_id):
    if profile not in PROFILE_PORT or strategy not in {
        "native_priority",
        "native_qbr",
        "reactive",
        "predictive_synthetic_lab",
    }:
        raise IntegrationError("Unknown experiment profile or strategy")
    updated = policy_body(original)
    rule = client.managed_rule(updated)
    rule["classification"] = {
        "application": {
            "appId": application_id,
            "category": "",
            "serviceClass": "",
            "importance": "",
        }
    }
    labels = [{"_id": client.config.links[link].path_label_id} for link in ("wan1", "wan2", "wan3")]
    rule["action"] = {
        "order": "priority",
        "fallback": "drop",
        "links": [{"order": "link-quality", "pathlabels": labels}]
        if strategy == "native_qbr"
        else [{"order": "priority", "pathlabels": [label]} for label in labels],
    }
    return updated
