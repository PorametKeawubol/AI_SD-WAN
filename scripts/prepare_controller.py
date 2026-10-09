"""Generate ignored secrets/certificate and Compose environment before deployment."""

import argparse
import ipaddress
import os
import secrets
from pathlib import Path

from lab_native import certificate

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller-ip", default="192.168.56.1")
    args = parser.parse_args()
    ip = str(ipaddress.IPv4Address(args.controller_ip))
    runtime = ROOT / "lab/runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "logs").mkdir(exist_ok=True)
    envfile = runtime / "controller.env"
    if envfile.exists():
        raise RuntimeError("Controller environment already exists; existing secrets were preserved")
    certificate(runtime / "certs", extra_ip=ip)
    values = {
        "USER_SECRET_KEY": secrets.token_urlsafe(48),
        "DEVICE_SECRET_KEY": secrets.token_urlsafe(48),
        "ENVIRONMENT": "isolated-lab",
        "HTTPS_PORT": "3443",
        "HTTP_PORT": "3080",
        "REST_SERVER_URL": f"https://{ip}:3443",
        "UI_SERVER_URL": f"https://{ip}:3443",
        "AGENT_BROKER": f"{ip}:3443",
        "CORS_WHITE_LIST": f"https://{ip}:3443",
        "HTTPS_CERT_KEY": "cert.ai-lab/domain.key",
        "HTTPS_CERT": "cert.ai-lab/certificate.pem",
        "MONGO_URL": "mongodb://mongo:27017/flexiwan?replicaSet=rs",
        "MONGO_ANALYTICS_URL": "mongodb://mongo:27017/flexiwanAnalytics?replicaSet=rs",
        "MONGO_BILLING_URL": "mongodb://mongo:27017/flexibilling?replicaSet=rs",
        "MONGO_VPN_URL": "mongodb://mongo:27017/flexivpn?replicaSet=rs",
        "REDIS_URL": "redis://redis:6379",
        "MAILER_HOST": "mail",
        "MAILER_PORT": "1025",
        "MAILER_BYPASS_CERT": "true",
        "ALLOW_USERS_REGISTRATION": "true",
        "USE_FLEXI_BILLING": "false",
        "STATS_POLL_PERIOD": "5000",
        "USER_IP_REQ_RATE_LIMIT": "6000",
        "LOG_LEVEL": "warn",
        "VALIDATE_OPEN_API_RESPONSE": "false",
    }
    envfile.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8"
    )
    os.chmod(envfile, 0o600)
    print("Generated lab/runtime/controller.env and CA. No container or VM was started.")


if __name__ == "__main__":
    main()
