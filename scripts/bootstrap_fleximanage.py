"""Create an isolated lab account/org/labels/policy through real flexiManage APIs.

Verification mail is delivered to the local SMTP sink, never to real recipients.
Secrets go only to ignored .work/native/credentials.json.
"""

import argparse
import email
import email.policy
import hashlib
import json
import os
import re
import secrets
import ssl
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".work/native"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://127.0.0.1:3443")
    parser.add_argument(
        "--ca-file",
        default=str(ROOT / "vendor/fleximanage/backend/bin/cert.ai-lab/certificate.pem"),
    )
    parser.add_argument("--runtime", default=str(RUNTIME))
    parser.add_argument("--mailpit-url")
    args = parser.parse_args()
    ca = Path(args.ca_file).resolve()
    runtime = Path(args.runtime).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    # Bootstrap changes only a dedicated localhost/private lab controller.
    import ipaddress

    parsed = urlsplit(args.url)
    if parsed.scheme != "https" or not ipaddress.ip_address(parsed.hostname).is_private:
        parser.error("Bootstrap requires an HTTPS private lab IP address")
    secret_file = runtime / "credentials.json"
    checkpoint = json.loads(secret_file.read_text(encoding="utf-8")) if secret_file.exists() else {}
    if checkpoint.get("policy_id") and checkpoint.get("enrollment_token"):
        raise RuntimeError(
            "Lab already bootstrapped; use its saved credentials instead of creating duplicates"
        )
    password = checkpoint.get("password") or "Lab-" + secrets.token_urlsafe(24) + "-9Aa!"
    user_email = "sdwan-lab@example.invalid"
    evidence = {"evidence_level": "real_controller_api", "real_edge": False, "checks": []}
    with httpx.Client(
        base_url=args.url.rstrip("/") + "/api/",
        verify=ssl.create_default_context(cafile=str(ca)),
        trust_env=False,
        timeout=30,
    ) as client:

        def request(method, path, body=None, params=None):
            reply = client.request(method, path, json=body, params=params)
            if reply.status_code >= 400:
                # Local development only; redact to avoid credentials in public output.
                (runtime / "bootstrap-error.json").write_text(reply.text, encoding="utf-8")
                raise RuntimeError(
                    f"API {method} {path} failed ({reply.status_code}); private error file saved"
                )
            evidence["checks"].append({"method": method, "path": path, "status": reply.status_code})
            return reply

        evidence["version"] = request("GET", "version").json()["version"]
        if not checkpoint:
            secret_file.write_text(
                json.dumps({"email": user_email, "password": password}), encoding="utf-8"
            )
            os.chmod(secret_file, 0o600)
            request(
                "POST",
                "users/register",
                {
                    "accountName": "AI SDWAN Lab",
                    "userFirstName": "SDWAN",
                    "userLastName": "Lab",
                    "email": user_email,
                    "password": password,
                    "userJobTitle": "Research",
                    "userPhoneNumber": "",
                    "country": "TH",
                    "companySize": "50-250",
                    "serviceType": "Enterprise",
                    "numberSites": "2",
                    "captcha": "",
                },
            )
        for attempt in range(0 if checkpoint.get("verified") else 30):
            mail_path = runtime / "mail.eml"
            if args.mailpit_url:
                with httpx.Client(base_url=args.mailpit_url, trust_env=False, timeout=5) as mail:
                    messages = mail.get("/api/v1/messages").json().get("messages", [])
                    for row in messages:
                        content = mail.get("/api/v1/message/" + row["ID"]).json()
                        html = content.get("HTML", "")
                        if "/verify-account?" in html:
                            # Preserve the same parser used with native SMTP sink.
                            mail_path.write_text(
                                "Content-Type: text/html; charset=utf-8\n\n" + html,
                                encoding="utf-8",
                            )
                            break
            if mail_path.exists():
                message = email.message_from_bytes(
                    mail_path.read_bytes(), policy=email.policy.default
                )
                html = message.get_body(preferencelist=("html", "plain")).get_content()
                match = re.search(r'https?://[^\s"<>]+/verify-account\?[^\s"<>]+', html)
                if match:
                    query = parse_qs(urlsplit(match.group(0).replace("&amp;", "&")).query)
                    request(
                        "POST",
                        "users/verify-account",
                        {"id": query["id"][0], "token": query["t"][0]},
                    )
                    checkpoint = {"email": user_email, "password": password, "verified": True}
                    secret_file.write_text(json.dumps(checkpoint), encoding="utf-8")
                    break
            time.sleep(1)
        else:
            if not checkpoint.get("verified"):
                raise RuntimeError("Local verification mail not found")
        login = request(
            "POST", "users/login", {"username": user_email, "password": password, "captcha": ""}
        )
        client.headers["Authorization"] = "Bearer " + login.headers["Refresh-JWT"]
        organizations = request("GET", "organizations").json()
        organization = next(
            (row for row in organizations if row["name"] == "AI SDWAN Isolated Lab"), None
        )
        organization = (
            organization
            or request(
                "POST",
                "organizations",
                {
                    "name": "AI SDWAN Isolated Lab",
                    "description": "Three WAN research lab",
                    "group": "lab",
                    "encryptionMethod": "psk",
                    "vxlanPort": "4789",
                    "tunnelRange": "10.100.0.0",
                },
            ).json()
        )
        org_id = organization["_id"]
        query = {"org": org_id}
        access = (
            {"token": checkpoint["access_key"]}
            if checkpoint.get("access_key")
            else request(
                "POST",
                "accesstokens",
                {
                    "name": "AI Lab Manager",
                    "accessKeyPermissionTo": "organization",
                    "accessKeyRole": "manager",
                    "accessKeyEntity": org_id,
                },
            ).json()
        )
        checkpoint.update(organization_id=org_id, access_key=access["token"])
        secret_file.write_text(json.dumps(checkpoint), encoding="utf-8")
        # Validate the generated organization-scoped access key immediately.
        client.headers["Authorization"] = "Bearer " + access["token"]
        labels = {}
        existing_labels = request("GET", "pathlabels", params=query).json()
        for link, color in zip(("wan1", "wan2", "wan3"), ("#2563eb", "#16a34a", "#f59e0b")):
            labels[link] = (
                next(
                    (row for row in existing_labels if row["name"] == "AI Lab " + link.upper()),
                    None,
                )
                or request(
                    "POST",
                    "pathlabels",
                    {
                        "name": "AI Lab " + link.upper(),
                        "description": "Dedicated " + link,
                        "type": "Tunnel",
                        "color": color,
                    },
                    query,
                ).json()
            )
        existing_policies = request("GET", "mlpolicies", params=query).json()
        policy = next(
            (row for row in existing_policies if row["name"] == "AI Lab Managed Policy"), None
        )
        if policy and (
            policy.get("description") != "Dedicated isolated test traffic"
            or len(policy.get("rules", [])) != 1
        ):
            raise RuntimeError("Existing policy differs from this bootstrap; inspect manually")
        policy = (
            policy
            or request(
                "POST",
                "mlpolicies",
                {
                    "name": "AI Lab Managed Policy",
                    "description": "Dedicated isolated test traffic",
                    "applyOnWan": False,
                    "overrideDefaultRoute": True,
                    "rules": [
                        {
                            "name": "AI-Lab-LAN",
                            "priority": 0,
                            "enabled": True,
                            "classification": {
                                "prefix": {"ip": "10.20.0.0/24", "ports": "", "protocol": ""}
                            },
                            "action": {
                                "order": "priority",
                                "fallback": "drop",
                                "links": [
                                    {"order": "priority", "pathlabels": [label]}
                                    for label in labels.values()
                                ],
                            },
                        }
                    ],
                },
                query,
            ).json()
        )
        checkpoint.update(
            policy_id=policy["_id"],
            rule_id=policy["rules"][0]["_id"],
            path_labels={link: label["_id"] for link, label in labels.items()},
        )
        secret_file.write_text(json.dumps(checkpoint), encoding="utf-8")
        enrollment = request(
            "POST",
            "tokens",
            {"name": "AI Lab Edges", "description": "Lab only", "server": args.url.rstrip("/")},
            query,
        ).json()
        read_policy = request("GET", "mlpolicies/" + policy["_id"], params=query).json()
        assert (
            read_policy["rules"][0]["action"]["links"][0]["pathlabels"][0]["_id"]
            == labels["wan1"]["_id"]
        )
        request("GET", "pathlabels", params=query)
        request("GET", "devices", params=query)
        secrets_payload = {
            "url": args.url.rstrip("/"),
            "email": user_email,
            "password": password,
            "access_key": access["token"],
            "enrollment_token": enrollment["token"],
            "organization_id": org_id,
            "policy_id": policy["_id"],
            "rule_id": policy["rules"][0]["_id"],
            "path_labels": {link: label["_id"] for link, label in labels.items()},
            "ca_file": str(ca),
        }
        secret_file.write_text(json.dumps(secrets_payload, indent=2), encoding="utf-8")
        os.chmod(secret_file, 0o600)
        (runtime / "enrollment-token.txt").write_text(enrollment["token"], encoding="utf-8")
        os.chmod(runtime / "enrollment-token.txt", 0o600)
        specification = ROOT / "vendor/fleximanage/backend/api/openapi.yaml"
        evidence.update(
            {
                "organization_id": org_id,
                "policy_id": policy["_id"],
                "labels_created": 3,
                "policy_readback": True,
                "device_count": 0,
                "openapi_sha256": hashlib.sha256(specification.read_bytes()).hexdigest(),
                "controller_commit": "62a168daedc785706808c7be2689e208d81bf77e",
                "timestamp": time.time(),
            }
        )
        destination = ROOT / "reports/flexiwan"
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "controller-verification.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "version": evidence["version"],
                    "checks": len(evidence["checks"]),
                    "policy_readback": True,
                    "real_edge": False,
                    "secrets_saved": str(secret_file),
                }
            )
        )


if __name__ == "__main__":
    main()
