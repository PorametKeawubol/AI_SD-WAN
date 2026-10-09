"""Generate owned lab database/API secrets and TLS leaf certificates once."""

import datetime as dt
import ipaddress
import json
import os
import secrets
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "lab/runtime/observability"


def prepare():
    if os.name != "posix" or os.geteuid() != 0 or not (ROOT / ".ai-sdwan-owned").is_file():
        raise RuntimeError("Use root in the owned Linux lab")
    private = RUNTIME / "private.json"
    if private.exists():
        ensure_control_token()
        print("Existing observability secrets and certificates preserved.")
        return
    if RUNTIME.exists() and any(RUNTIME.iterdir()):
        raise RuntimeError("Partial observability runtime preserved; inspect before preparing")
    RUNTIME.mkdir(parents=True, mode=0o700, exist_ok=True)
    ca = x509.load_pem_x509_certificate((ROOT / "lab/runtime/certs/certificate.pem").read_bytes())
    ca_key = serialization.load_pem_private_key(
        (ROOT / "lab/runtime/certs/domain.key").read_bytes(), password=None
    )
    if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise RuntimeError("The existing lab certificate is not a CA")
    values = {
        name: secrets.token_hex(32)
        for name in (
            "postgres_password",
            "sdwan_password",
            "reader_password",
            "grafana_db_password",
            "grafana_password",
            "api_token",
        )
    }
    values["grafana_user"] = "admin"
    values["database_url"] = (
        "postgresql://sdwan_app:"
        + values["sdwan_password"]
        + "@localhost:5432/sdwan?sslmode=verify-full&sslrootcert="
        + str(ROOT / "lab/runtime/certs/certificate.pem")
    )
    now = dt.datetime.now(dt.UTC)
    for service, uid in (("api", 0), ("database", 70), ("grafana", 472)):
        directory = RUNTIME / "certs" / service
        directory.mkdir(parents=True, mode=0o755)
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        names = [x509.DNSName(name) for name in ("localhost", "timescaledb", "grafana", "api")]
        names += [
            x509.IPAddress(ipaddress.ip_address(value)) for value in ("127.0.0.1", "192.168.56.1")
        ]
        certificate = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AI SD-WAN " + service)])
            )
            .issuer_name(ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(min(now + dt.timedelta(days=80), ca.not_valid_after_utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        key_path = directory / "server.key"
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        os.chown(key_path, uid, uid)
        key_path.chmod(0o600)
        (directory / "server.crt").write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
            + ca.public_bytes(serialization.Encoding.PEM)
        )
        (directory / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    # These files contain generated hexadecimal secrets, never values from user input.
    init = """CREATE ROLE sdwan_app LOGIN PASSWORD '{sdwan_password}';
CREATE ROLE sdwan_reader LOGIN PASSWORD '{reader_password}';
CREATE ROLE grafana_app LOGIN PASSWORD '{grafana_db_password}';
CREATE DATABASE sdwan OWNER sdwan_app;
CREATE DATABASE grafana OWNER grafana_app;
\\connect sdwan
CREATE EXTENSION IF NOT EXISTS timescaledb;
GRANT CONNECT ON DATABASE sdwan TO sdwan_reader;
GRANT USAGE ON SCHEMA public TO sdwan_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE sdwan_app IN SCHEMA public GRANT SELECT ON TABLES TO sdwan_reader;
""".format(**values)
    (RUNTIME / "init.sql").write_text(init, encoding="utf-8")
    (RUNTIME / "database.env").write_text(
        "POSTGRES_PASSWORD=" + values["postgres_password"] + "\nPOSTGRES_DB=postgres\n",
        encoding="utf-8",
    )
    (RUNTIME / "grafana.env").write_text(
        "GF_SECURITY_ADMIN_USER=admin\nGF_SECURITY_ADMIN_PASSWORD="
        + values["grafana_password"]
        + "\nGF_DATABASE_PASSWORD="
        + values["grafana_db_password"]
        + "\nSDWAN_READER_PASSWORD="
        + values["reader_password"]
        + "\n",
        encoding="utf-8",
    )
    private.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    for file in (private, RUNTIME / "init.sql", RUNTIME / "database.env", RUNTIME / "grafana.env"):
        file.chmod(0o600)
    os.chown(RUNTIME / "init.sql", 70, 70)
    ensure_control_token()
    print("Prepared private SQL/API credentials and CA-signed TLS certificates.")


def ensure_control_token():
    """Provision the existing API token to Grafana without rotating any secrets."""
    values = json.loads((RUNTIME / "private.json").read_text(encoding="utf-8"))
    path = RUNTIME / "grafana.env"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith("SDWAN_CONTROL_API_TOKEN=")]
    lines.append("SDWAN_CONTROL_API_TOKEN=" + values["api_token"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


if __name__ == "__main__":
    prepare()
