"""Bounded HTTP workloads measured at the receiving application endpoint.

Run the server and client in the two LAN namespaces to exercise flexiEdge's
data plane. Loopback tests verify this tool only, not SD-WAN forwarding.
Direct HTTP connections deliberately ignore proxy environment variables.
"""

import argparse
import http.client
import ipaddress
import json
import math
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
SMALL_BODY = b'{"status":"ok","service":"ai-sdwan-application"}\n'
HEALTH_BODY = b'{"status":"ok","service":"ai-sdwan-application","schema":1}\n'
DOWNLOAD_CHUNK = bytes(range(256)) * 256


def _number(name, value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid {name}")  # noqa: TRY004 - CLI validation is a ValueError contract.
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} outside bounded lab limits")


def _endpoint(host, port, *, ephemeral=False):
    if not isinstance(host, str) or ipaddress.ip_address(host).version != 4:
        raise ValueError("Host must be a literal IPv4 address")
    if type(port) is not int or not (0 if ephemeral else 1) <= port <= 65535:
        raise ValueError("Invalid HTTP port")


class ApplicationHandler(BaseHTTPRequestHandler):
    """Serve generated content only; never expose local files or proxy URLs."""

    server_version = "AI-SDWAN-Lab-Application"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        request = urlsplit(self.path)
        if request.scheme or request.netloc or request.fragment:
            self.send_error(400)
            return
        if request.path in ("/health", "/small") and not request.query:
            body = HEALTH_BODY if request.path == "/health" else SMALL_BODY
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if request.path != "/download":
            self.send_error(404)
            return
        query = parse_qs(request.query, keep_blank_values=True)
        values = query.get("bytes", [str(MAX_DOWNLOAD_BYTES)])
        if set(query) - {"bytes"} or len(values) != 1 or not values[0].isascii():
            self.send_error(400)
            return
        if not values[0].isdigit() or len(values[0]) > 8:
            self.send_error(400)
            return
        size = int(values[0])
        if not 1 <= size <= MAX_DOWNLOAD_BYTES:
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        try:
            for offset in range(0, size, len(DOWNLOAD_CHUNK)):
                self.wfile.write(DOWNLOAD_CHUNK[: min(len(DOWNLOAD_CHUNK), size - offset)])
        except (BrokenPipeError, ConnectionResetError):
            pass


def create_server(host="10.30.0.2", port=8088):
    """Return a server; port zero is supported for isolated loopback tests."""
    _endpoint(host, port, ephemeral=True)
    server = ThreadingHTTPServer((host, port), ApplicationHandler)
    server.daemon_threads = True
    return server


def wait_for_http(host, port, timeout=15):
    """Wait for this endpoint, excluding server cold start from measurements."""
    _endpoint(host, port)
    _number("readiness timeout", timeout, 0.1, 30)
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        budget = min(0.5, deadline - time.perf_counter())
        if budget <= 0:
            break
        attempt_deadline = time.perf_counter() + budget
        connection = http.client.HTTPConnection(host, port, timeout=budget)
        active_socket = None

        def remaining(current_socket, expires=attempt_deadline):
            left = expires - time.perf_counter()
            if left <= 0:
                raise TimeoutError("Readiness attempt deadline reached")
            if current_socket:
                current_socket.settimeout(left)

        try:
            connection.connect()
            active_socket = connection.sock
            remaining(active_socket)
            connection.request("GET", "/health", headers={"Connection": "close"})
            remaining(active_socket)
            response = connection.getresponse()
            if response.status != 200 or response.getheader("Content-Length") != str(
                len(HEALTH_BODY)
            ):
                raise RuntimeError("HTTP readiness endpoint is an unexpected service")
            body = bytearray()
            while len(body) < len(HEALTH_BODY):
                remaining(active_socket)
                chunk = response.read1(len(HEALTH_BODY) - len(body))
                if not chunk:
                    break
                body.extend(chunk)
            if bytes(body) != HEALTH_BODY:
                raise RuntimeError("HTTP readiness endpoint is an unexpected service")
            return True
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        pause = min(0.2, deadline - time.perf_counter())
        if pause > 0:
            time.sleep(pause)
    raise RuntimeError("HTTP application server did not become ready before the deadline")


def measure_request(host, port, workload, *, download_bytes=MAX_DOWNLOAD_BYTES, timeout=3.0):
    """Measure a direct request, preserving failure and partial byte evidence."""
    _endpoint(host, port)
    _number("timeout", timeout, 0.01, 30)
    if workload not in ("small", "download"):
        raise ValueError("Unknown HTTP workload")
    if type(download_bytes) is not int or not 1 <= download_bytes <= MAX_DOWNLOAD_BYTES:
        raise ValueError("Download bytes outside bounded lab limits")
    path = "/small" if workload == "small" else f"/download?bytes={download_bytes}"
    expected = len(SMALL_BODY) if workload == "small" else download_bytes
    timestamp, started = time.time(), time.perf_counter()
    deadline = started + timeout
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    active_socket = None
    row = {
        "schema": 1,
        "measurement": "http_end_to_end",
        "application": "http" if workload == "small" else "download",
        "workload": workload,
        "timestamp": timestamp,
        "target": host,
        "destination_port": port,
        "requested_bytes": expected,
        "received_bytes": 0,
        "http_status": None,
        "success": False,
        "response_latency_ms": None,
        "latency_ms": None,
        "completion_latency_ms": None,
        "goodput_mbps": None,
        "received_mbps": None,
        "error": None,
    }

    def remaining():
        value = deadline - time.perf_counter()
        if value <= 0:
            raise TimeoutError("Request deadline reached")
        if active_socket:
            # HTTPResponse may retain a socket file after HTTPConnection closes
            # its own reference for a Connection: close response.
            active_socket.settimeout(value)
        return value

    try:
        connection.connect()
        active_socket = connection.sock
        remaining()
        connection.request("GET", path, headers={"Connection": "close"})
        remaining()
        response = connection.getresponse()
        row["http_status"] = response.status
        row["response_latency_ms"] = (time.perf_counter() - started) * 1000
        if response.status != 200:
            row["error"] = "http_status"
        elif response.getheader("Content-Length") != str(expected):
            row["error"] = "unexpected_content_length"
        else:
            while row["received_bytes"] < expected:
                remaining()
                chunk = response.read1(min(65536, expected - row["received_bytes"]))
                if not chunk:
                    break
                row["received_bytes"] += len(chunk)
            if row["received_bytes"] == expected:
                row["success"] = True
            else:
                row["error"] = "incomplete_body"
    except http.client.IncompleteRead as error:
        row["received_bytes"] += len(error.partial)
        row["error"] = "IncompleteRead"
    except (OSError, http.client.HTTPException) as error:
        # Exception type avoids logging arbitrary server text or private headers.
        row["error"] = type(error).__name__
    finally:
        connection.close()
    elapsed = max(time.perf_counter() - started, 1e-9)
    row["finished_at"] = time.time()
    row["elapsed_ms"] = elapsed * 1000
    if row["success"]:
        row["completion_latency_ms"] = elapsed * 1000
        row["latency_ms"] = elapsed * 1000
        row["goodput_mbps"] = row["received_bytes"] * 8 / elapsed / 1e6
        row["received_mbps"] = row["goodput_mbps"]
    return row


def _atomic_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            name = stream.name
            json.dump(document, stream, allow_nan=False)
            stream.flush()
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def run_client(
    host,
    port,
    duration,
    output,
    *,
    interval=1.0,
    download_interval=5.0,
    download_bytes=MAX_DOWNLOAD_BYTES,
    timeout=3.0,
    latest=None,
):
    """Run independent small requests and downloads without catch-up bursts."""
    _endpoint(host, port)
    _number("duration", duration, 1, 600)
    _number("interval", interval, 0.2, 60)
    _number("download interval", download_interval, 1, 60)
    _number("timeout", timeout, 0.1, 30)
    if type(download_bytes) is not int or not 1 <= download_bytes <= MAX_DOWNLOAD_BYTES:
        raise ValueError("Download bytes outside bounded lab limits")
    output = Path(output)
    if latest is not None and output.resolve() == Path(latest).resolve():
        raise ValueError("JSONL and latest JSON paths must differ")
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    deadline = started + duration
    lock = threading.Lock()
    stopped = threading.Event()
    worker_errors = []
    summary = {
        "schema": 1,
        "measurement": "http_end_to_end",
        "started_at": time.time(),
        "finished_at": None,
        "active": True,
        "samples": {},
        "counts": {
            key: {"requests": 0, "successes": 0, "failures": 0} for key in ("small", "download")
        },
    }

    with output.open("w", encoding="utf-8") as stream:

        def workload_loop(workload, cadence):
            while not stopped.is_set() and time.perf_counter() < deadline:
                request_started = time.perf_counter()
                budget = min(timeout, deadline - request_started)
                if budget < 0.01:
                    break
                row = measure_request(
                    host, port, workload, download_bytes=download_bytes, timeout=budget
                )
                row["second"] = request_started - started
                with lock:
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
                    stream.flush()
                    summary["samples"][workload] = row
                    counts = summary["counts"][workload]
                    counts["requests"] += 1
                    counts["successes" if row["success"] else "failures"] += 1
                    if latest is not None:
                        _atomic_json(latest, summary)
                # A slow response delays the next request; never catch up in a burst.
                pause = min(request_started + cadence, deadline) - time.perf_counter()
                if pause > 0:
                    stopped.wait(pause)

        def worker(workload, cadence):
            try:
                workload_loop(workload, cadence)
            except Exception as error:  # noqa: BLE001 - propagate worker failures to the caller.
                with lock:
                    worker_errors.append(error)
                stopped.set()

        threads = [
            threading.Thread(target=worker, args=("small", interval)),
            threading.Thread(target=worker, args=("download", download_interval)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    summary["active"] = False
    summary["finished_at"] = time.time()
    if worker_errors:
        raise RuntimeError("HTTP application measurement worker stopped") from worker_errors[0]
    if latest is not None:
        _atomic_json(latest, summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("server", "client", "ready"))
    parser.add_argument("--host", default="10.30.0.2")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--download-interval", type=float, default=5)
    parser.add_argument("--download-bytes", type=int, default=MAX_DOWNLOAD_BYTES)
    parser.add_argument("--timeout", type=float, default=3)
    parser.add_argument("--ready-timeout", type=float, default=15)
    parser.add_argument("--output", default="reports/live/application-http.jsonl")
    parser.add_argument("--latest")
    args = parser.parse_args()
    try:
        if args.action == "server":
            _endpoint(args.host, args.port)
            with create_server(args.host, args.port) as server:
                server.serve_forever()
        elif args.action == "ready":
            wait_for_http(args.host, args.port, args.ready_timeout)
        else:
            run_client(
                args.host,
                args.port,
                args.duration,
                args.output,
                interval=args.interval,
                download_interval=args.download_interval,
                download_bytes=args.download_bytes,
                timeout=args.timeout,
                latest=args.latest,
            )
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
