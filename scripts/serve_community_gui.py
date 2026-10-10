"""Serve the pinned community frontend through a loopback, read-only lab proxy.

The GUI comes from Aikonlee/fleximanage. This helper only serves its build and
connects supported read endpoints to the existing, TLS-verified controller.
"""

import argparse
import json
import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "vendor/community-fleximanage/frontend/build"
RUNTIME = ROOT / "lab/runtime/community-gui"
PORT = 18443
CONTROLLER = "https://192.168.56.1:3443"
OBJECT_ID = r"[0-9a-f]{24}"
READ_PATHS = re.compile(
    rf"(?:version|configuration/restServers|organizations(?:/(?:summary|{OBJECT_ID}))?"
    rf"|devices(?:/{OBJECT_ID}(?:/(?:interfaces/status|routes|routing/ospf|health|"
    rf"statistics|status|tunnels|tunnelstats))?)?"
    rf"|(?:tunnels|pathlabels|mlpolicies|qospolicies|firewallpolicies|notifications|"
    rf"jobs|appidentifications|peers|applications|members)(?:/{OBJECT_ID})?"
    rf"|devicestats(?:/{OBJECT_ID})?)"
)


def owned_process(pid):
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        return str(Path(__file__).resolve()).encode() in args and b"--worker" in args
    except OSError:
        return False


def create_app():
    credentials = json.loads((ROOT / "lab/runtime/credentials.json").read_text())
    organization_id = credentials["organization_id"]
    lab_email = credentials["email"]
    ca = ssl.create_default_context(cafile=str(ROOT / "lab/runtime/certs/certificate.pem"))

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            base_url=CONTROLLER + "/api/",
            verify=ca,
            trust_env=False,
            follow_redirects=False,
            timeout=25,
        ) as client:
            version = await client.get("version")
            if version.status_code != 200 or version.json().get("version") != "6.3.37":
                raise RuntimeError("The original controller did not verify as 6.3.37")
            app.state.client = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_origin_only(request, call_next):
        expected_hosts = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
        if request.headers.get("host", "").lower() not in expected_hosts:
            return JSONResponse({"error": "This trial is available only on localhost"}, 403)
        origin = request.headers.get("origin")
        if origin and origin not in {f"https://{host}" for host in expected_hosts}:
            return JSONResponse({"error": "Cross-origin requests are not allowed"}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Trial-Mode"] = "read-only"
        return response

    @app.get("/trial/status")
    async def trial_status():
        return {
            "frontend": "Aikonlee/fleximanage community GUI",
            "controller_version": "6.3.37",
            "read_only": True,
            "tls_verified": True,
        }

    @app.get("/trial/source.zip")
    async def source():
        return FileResponse(RUNTIME / "source.zip", media_type="application/zip")

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request):
        # Some upstream GET routes reset MFA; only known read resources are allowed.
        is_read = request.method == "GET" and READ_PATHS.fullmatch(path)
        is_login = request.method == "POST" and path == "users/login"
        if not (is_read or is_login):
            return JSONResponse(
                {"error": "GUI trial is read-only. Configuration changes are disabled."}, 403
            )
        body = await request.body()
        if len(body) > 8192:
            return JSONResponse({"error": "Request body is too large"}, 413)
        if is_login:
            try:
                data = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                return JSONResponse({"error": "Invalid login request"}, 400)
            if not isinstance(data, dict) or data.get("username") != lab_email:
                return JSONResponse({"error": "Use the existing isolated lab account"}, 403)
            body = json.dumps(
                {key: data.get(key, "") for key in ("username", "password", "captcha")}
            ).encode()
        query = list(request.query_params.multi_items())
        if any(key == "org" and value != organization_id for key, value in query):
            return JSONResponse({"error": "This trial is restricted to the lab organization"}, 403)
        if is_read and not any(key == "org" for key, _ in query):
            query.append(("org", organization_id))
        headers = {"content-type": "application/json"}
        for name in ("authorization", "refresh-token"):
            if name in request.headers:
                headers[name] = request.headers[name]
        try:
            upstream = await request.app.state.client.request(
                request.method, path, params=query, content=body, headers=headers
            )
        except httpx.HTTPError:
            return JSONResponse({"error": "The local controller is temporarily unavailable"}, 502)
        response_headers = {
            name: upstream.headers[name]
            for name in ("content-type", "refresh-jwt", "refresh-token", "records-total")
            if name in upstream.headers
        }
        return Response(upstream.content, upstream.status_code, headers=response_headers)

    @app.get("/{path:path}")
    async def frontend(path: str):
        candidate = (BUILD / path).resolve()
        if not candidate.is_relative_to(BUILD.resolve()):
            return JSONResponse({"error": "Invalid asset path"}, 404)
        # The upstream public directory also contains obsolete auth-debug HTML.
        # Serve only normal build assets; SPA routes use the injected index below.
        is_asset = path.startswith("static/") or path in {"manifest.json", "asset-manifest.json"}
        if candidate.is_file() and is_asset:
            return FileResponse(candidate)
        if path.startswith("static/") or Path(path).suffix:
            return JSONResponse({"error": "Asset not found"}, 404)
        html = (BUILD / "index.html").read_text()
        config = json.dumps({"baseUrl": "/api", "captchaEnforced": False, "trialReadOnly": True})
        html, count = re.subn(
            r"window\.__FLEXIWAN_SERVER_CONFIG__\s*=\s*\{\}",
            "window.__FLEXIWAN_SERVER_CONFIG__=" + config,
            html,
            count=1,
        )
        if count != 1:
            raise RuntimeError("The pinned frontend config injection point was not found")
        banner = (
            '<aside style="position:fixed;bottom:10px;right:14px;z-index:10000;'
            "background:#fff3cd;border:1px solid #e0c36b;border-radius:6px;"
            'padding:8px 12px;font:13px system-ui;color:#664d03">'
            "Community GUI · โหมดทดลอง: อ่านข้อมูลอย่างเดียว · "
            '<a href="/trial/source.zip">Source</a></aside>'
        )
        return HTMLResponse(html.replace("</body>", banner + "</body>"))

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name != "posix" or os.environ.get("WSL_DISTRO_NAME") != "AI-SDWAN-Lab":
        raise RuntimeError("Use the owned AI-SDWAN-Lab distribution")
    if ROOT != Path("/opt/ai-sdwan-lab") or not (ROOT / ".ai-sdwan-owned").is_file():
        raise RuntimeError("The existing lab ownership marker did not verify")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    pid_file = RUNTIME / "pid"
    pid = int(pid_file.read_text()) if pid_file.exists() else None
    if args.stop:
        if pid and owned_process(pid):
            os.kill(pid, signal.SIGTERM)
            for _ in range(100):
                if not owned_process(pid):
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError(
                    "Community GUI is still stopping; retry after current reads finish"
                )
            print("Community GUI trial stopped; controller and edges remain running.")
        else:
            print("Community GUI trial is already stopped.")
        return
    if not (BUILD / "index.html").is_file() or not (RUNTIME / "source.zip").is_file():
        raise RuntimeError("Build and prepare the pinned community frontend first")
    if args.background:
        if pid and owned_process(pid):
            print(f"Community GUI already running: https://localhost:{PORT}/")
            return
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", PORT))
        with (RUNTIME / "server.log").open("ab") as log:
            child = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--worker"],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        pid_file.write_text(str(child.pid))
        os.chmod(pid_file, 0o600)
        ca = ssl.create_default_context(
            cafile=str(ROOT / "lab/runtime/observability/certs/api/ca.crt")
        )
        with httpx.Client(verify=ca, trust_env=False, timeout=2) as client:
            for _ in range(30):
                try:
                    response = client.get(f"https://localhost:{PORT}/trial/status")
                    if response.status_code == 200 and response.json().get("read_only") is True:
                        print(f"Community GUI trial: https://localhost:{PORT}/")
                        return
                except httpx.HTTPError:
                    pass
                if child.poll() is not None:
                    raise RuntimeError("Community GUI failed to start; inspect its private log")
                time.sleep(0.25)
        raise RuntimeError("Community GUI did not respond in time; inspect its private log")
    cert_dir = ROOT / "lab/runtime/observability/certs/api"
    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=PORT,
        ssl_certfile=str(cert_dir / "server.crt"),
        ssl_keyfile=str(cert_dir / "server.key"),
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
