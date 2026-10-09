"""Fetch pinned official Swagger UI assets used by FastAPI's local API documentation."""

import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fetch():
    with urllib.request.urlopen(
        "https://registry.npmjs.org/swagger-ui-dist/latest", timeout=30
    ) as response:
        package = json.load(response)
    with urllib.request.urlopen(package["dist"]["tarball"], timeout=45) as response:
        data = response.read()
    expected = package["dist"]["integrity"]
    actual = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if expected != actual:
        raise RuntimeError("Swagger package integrity mismatch")
    target = ROOT / "sdwan/openapi_assets"
    target.mkdir(exist_ok=True)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for name in ("swagger-ui-bundle.js", "swagger-ui.css", "favicon-32x32.png", "LICENSE"):
            content = archive.extractfile("package/" + name).read()
            (target / name).write_bytes(content)
            files[name] = hashlib.sha256(content).hexdigest()
    lock = {
        "package": "swagger-ui-dist",
        "version": package["version"],
        "source": package["dist"]["tarball"],
        "integrity": actual,
        "files_sha256": files,
    }
    (ROOT / "lab/openapi-assets-lock.json").write_text(
        json.dumps(lock, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({"package": lock["package"], "version": lock["version"], "files": list(files)})
    )


if __name__ == "__main__":
    fetch()
