"""Isolated Windows flexiManage controller fallback; no Windows services installed.

Requires the pinned source in vendor/fleximanage, npm ci, and extracted portable
MongoDB/Redis under .work/runtime. This is a lab controller, not flexiEdge/VPP.
"""

import argparse
import datetime as dt
import ipaddress
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".work" / "native"


def certificate(directory, extra_ip=None):
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "certificate.pem").exists():
        return
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AI SD-WAN local lab")])
    now = dt.datetime.now(dt.UTC)
    names = [
        x509.DNSName("localhost"),
        x509.DNSName("controller"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    if extra_ip:
        names.append(x509.IPAddress(ipaddress.ip_address(extra_ip)))
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(names),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    (directory / "domain.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(directory / "domain.key", 0o600)
    (directory / "certificate.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def spawn(name, command, cwd, env=None):
    out = (RUNTIME / f"{name}.out.log").open("ab")
    err = (RUNTIME / f"{name}.err.log").open("ab")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=out, stderr=err, creationflags=flags
    )
    out.close()
    err.close()
    return process.pid


def smtp():
    from aiosmtpd.controller import Controller

    class Sink:
        async def handle_DATA(self, server, session, envelope):
            # All lab messages terminate here. Never relay to an external address.
            (RUNTIME / "mail.eml").write_bytes(envelope.content)
            return "250 OK"

    controller = Controller(Sink(), hostname="127.0.0.1", port=1025)
    controller.start()
    while True:
        time.sleep(1)


def start():
    from pymongo import MongoClient
    from pymongo.errors import OperationFailure, PyMongoError

    RUNTIME.mkdir(parents=True, exist_ok=True)
    for port in (27027, 6389, 1025, 3080, 3443):
        with socket.socket() as listener:
            if listener.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(
                    f"Lab port {port} is occupied; existing processes were preserved"
                )
    backend = ROOT / "vendor/fleximanage/backend"
    certdir = backend / "bin/cert.ai-lab"
    certificate(certdir)
    mongo = next((ROOT / ".work/runtime/mongo").rglob("mongod.exe"))
    redis = next((ROOT / ".work/runtime/redis").rglob("redis-server.exe"))
    (RUNTIME / "mongo-data").mkdir(exist_ok=True)
    pids = {}
    pids["mongo"] = spawn(
        "mongo",
        [
            str(mongo),
            "--bind_ip",
            "127.0.0.1",
            "--port",
            "27027",
            "--replSet",
            "rs",
            "--dbpath",
            str(RUNTIME / "mongo-data"),
        ],
        RUNTIME,
    )
    pids["redis"] = spawn(
        "redis",
        [str(redis), "--bind", "127.0.0.1", "--port", "6389", "--save", "", "--appendonly", "no"],
        redis.parent,
    )
    pids["smtp"] = spawn("smtp", [sys.executable, str(Path(__file__).resolve()), "smtp"], ROOT)
    (RUNTIME / "pids.json").write_text(json.dumps(pids), encoding="utf-8")
    direct = MongoClient(
        "mongodb://127.0.0.1:27027/?directConnection=true", serverSelectionTimeoutMS=1000
    )
    for attempt in range(45):
        try:
            direct.admin.command("ping")
            break
        except PyMongoError:
            time.sleep(1)
    try:
        direct.admin.command(
            "replSetInitiate",
            {
                "_id": "rs",
                "members": [{"_id": 0, "host": "127.0.0.1:27027"}],
            },
        )
    except OperationFailure as error:
        if error.code != 23:
            raise
    for attempt in range(45):
        if direct.admin.command("hello").get("isWritablePrimary"):
            break
        time.sleep(1)
    envfile = RUNTIME / "controller-env.json"
    if envfile.exists():
        overrides = json.loads(envfile.read_text(encoding="utf-8"))
    else:
        overrides = {
            "USER_SECRET_KEY": secrets.token_urlsafe(48),
            "DEVICE_SECRET_KEY": secrets.token_urlsafe(48),
        }
    overrides.update(
        {
            "HTTP_PORT": "3080",
            "HTTPS_PORT": "3443",
            "REDIRECT_HTTPS_PORT": "3443",
            "REST_SERVER_URL": "https://127.0.0.1:3443",
            "UI_SERVER_URL": "https://127.0.0.1:3443",
            "AGENT_BROKER": "127.0.0.1:3443",
            "CORS_WHITE_LIST": "https://127.0.0.1:3443",
            "HTTPS_CERT_KEY": "cert.ai-lab/domain.key",
            "HTTPS_CERT": "cert.ai-lab/certificate.pem",
            "MONGO_URL": "mongodb://127.0.0.1:27027/flexiwan?replicaSet=rs",
            "MONGO_ANALYTICS_URL": "mongodb://127.0.0.1:27027/flexiwanAnalytics?replicaSet=rs",
            "MONGO_BILLING_URL": "mongodb://127.0.0.1:27027/flexibilling?replicaSet=rs",
            "MONGO_VPN_URL": "mongodb://127.0.0.1:27027/flexivpn?replicaSet=rs",
            "REDIS_URL": "redis://127.0.0.1:6389",
            "MAILER_HOST": "127.0.0.1",
            "MAILER_PORT": "1025",
            "MAILER_BYPASS_CERT": "true",
            "ALLOW_USERS_REGISTRATION": "true",
            "USE_FLEXI_BILLING": "false",
            "STATS_POLL_PERIOD": "5000",
            "LOG_LEVEL": "warn",
            "USER_IP_REQ_RATE_LIMIT": "6000",
            "VALIDATE_OPEN_API_RESPONSE": "false",
            "ENVIRONMENT": "isolated-lab",
        }
    )
    envfile.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    os.chmod(envfile, 0o600)
    with (RUNTIME / "seed.log").open("ab") as log:
        subprocess.run(
            [shutil.which("node"), str(ROOT / "scripts/seed_controller.js")],
            cwd=backend,
            env={**os.environ, **overrides},
            stdout=log,
            stderr=log,
            check=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    pids["controller"] = spawn(
        "controller",
        [shutil.which("node"), "--require", str(ROOT / "scripts/loopback-only.cjs"), "bin/www"],
        backend,
        {**os.environ, **overrides},
    )
    (RUNTIME / "pids.json").write_text(json.dumps(pids), encoding="utf-8")
    with httpx.Client(
        verify=ssl.create_default_context(cafile=str(certdir / "certificate.pem")),
        trust_env=False,
        timeout=2,
    ) as client:
        for attempt in range(45):
            try:
                response = client.get("https://127.0.0.1:3443/api/version")
                response.raise_for_status()
                print(
                    json.dumps(
                        {
                            "controller": response.json(),
                            "url": "https://127.0.0.1:3443/api",
                            "evidence_level": "real_controller_only",
                        }
                    )
                )
                return
            except httpx.HTTPError:
                time.sleep(1)
    raise RuntimeError("Controller did not start; inspect private .work/native logs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "smtp"])
    arguments = parser.parse_args()
    smtp() if arguments.action == "smtp" else start()
