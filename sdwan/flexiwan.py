"""flexiManage 6.3.37 REST contract, with no implicit credentials or TLS bypass."""

import copy
import hashlib
import json
import os
import ssl
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain import LINKS

ObjectID = str
OPENAPI_SHA256 = "4a95b529264a201d083176dfeada3707add1d5ceeba17a01518ab9a733466664"


class WANBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path_label_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    capacity_mbps: float = Field(gt=0, le=100000)


class FlexiWANConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    organization_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    device_ids: list[str] = Field(min_length=1, max_length=2)
    policy_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    rule_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    links: dict[str, WANBinding]
    access_key_env: str = "FLEXIWAN_ACCESS_KEY"
    ca_file: str | None = None
    expected_version: Literal["6.3.37"] = "6.3.37"
    request_timeout_seconds: float = Field(default=20, gt=0, le=120)
    job_timeout_seconds: float = Field(default=180, gt=0, le=600)
    poll_seconds: float = Field(default=1, gt=0, le=10)
    journal_dir: str = "data/flexiwan-journal"
    telemetry_device_id: str | None = None
    probe_command: Literal["python3 /opt/ai-sdwan/probe.py --config /etc/ai-sdwan/probe.json"] = (
        "python3 /opt/ai-sdwan/probe.py --config /etc/ai-sdwan/probe.json"
    )

    @model_validator(mode="after")
    def validate_scope(self):
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/api"
        ):
            raise ValueError("base_url must be https://host:port/api with no credentials")
        if set(self.links) != set(LINKS):
            raise ValueError("exactly wan1, wan2, wan3 bindings required")
        if len({binding.path_label_id for binding in self.links.values()}) != 3:
            raise ValueError("WAN path labels must be distinct")
        if len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("duplicate devices")
        import re

        if any(not re.fullmatch(r"[0-9a-f]{24}", value) for value in self.device_ids):
            raise ValueError("invalid device ObjectId")
        if self.telemetry_device_id and self.telemetry_device_id not in self.device_ids:
            raise ValueError("telemetry device must be in the managed allowlist")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.access_key_env):
            raise ValueError("invalid environment variable name")
        return self

    @classmethod
    def load(cls, path):
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class IntegrationError(RuntimeError):
    """Safe public error: never include response bodies, URLs with keys or commands."""


def policy_body(policy):
    """Keep IDs: flexiManage recreates rules if omitted. Preserve all other rules."""
    fields = ("_id", "name", "description", "applyOnWan", "overrideDefaultRoute", "rules")
    result = {key: copy.deepcopy(policy[key]) for key in fields if key in policy}
    for rule in result["rules"]:
        for key in list(rule):
            if key not in {"_id", "name", "priority", "enabled", "classification", "action"}:
                del rule[key]
    return result


def label_id(label):
    return label.get("_id") if isinstance(label, dict) else label


def action_signature(action):
    return {
        "order": action.get("order"),
        "fallback": action.get("fallback"),
        "links": [
            {"order": link.get("order"), "labels": [label_id(x) for x in link["pathlabels"]]}
            for link in action.get("links", [])
        ],
    }


def classification_signature(classification):
    result = copy.deepcopy(classification)
    if not isinstance(result, dict):
        return result
    # The actual agent adds the unused application/prefix object and empty
    # wildcard fields. Keep every nonempty filter and every unknown field.
    for section, fields in (
        ("prefix", ("ip", "ports", "protocol")),
        ("application", ("appId", "category", "serviceClass", "importance")),
    ):
        filters = result.get(section)
        if isinstance(filters, dict):
            for field in fields:
                if filters.get(field) in ("", None):
                    filters.pop(field, None)
            if not filters:
                result.pop(section)
    return result


def policy_signature(policy):
    result = policy_body(policy)
    for rule in result["rules"]:
        rule["action"] = action_signature(rule["action"])
    return result


class FlexiManageClient:
    def __init__(self, config: FlexiWANConfig, client=None):
        self.config = config
        key = os.environ.get(config.access_key_env)
        if not key:
            raise IntegrationError(f"Set {config.access_key_env} before connecting")
        self.http = client or httpx.Client(
            base_url=config.base_url.rstrip("/") + "/",
            verify=ssl.create_default_context(cafile=config.ca_file),
            timeout=config.request_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        )
        self.key = key

    def request(self, method, path, *, body=None, params=None):
        query = {"org": self.config.organization_id, **(params or {})}
        try:
            response = self.http.request(
                method,
                path.lstrip("/"),
                params=query,
                json=body,
                headers={"Authorization": f"Bearer {self.key}"},
            )
            response.raise_for_status()
            self.last_job_location = response.headers.get("location")
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            raise IntegrationError(
                f"flexiManage request failed ({status or type(error).__name__})"
            ) from None

    def verify_version(self):
        version = self.request("GET", "version")
        # Official controller exposes {version: package.json.version}.
        if not isinstance(version, dict) or version.get("version") != self.config.expected_version:
            raise IntegrationError("Controller version differs from the pinned 6.3.37 contract")
        try:
            url = self.config.base_url.rsplit("/api", 1)[0] + "/spec"
            spec = self.http.get(url)
            spec.raise_for_status()
            checksum = hashlib.sha256(spec.content.replace(b"\r\n", b"\n")).hexdigest()
        except httpx.HTTPError:
            raise IntegrationError(
                "Cannot verify the running controller OpenAPI contract"
            ) from None
        if checksum != OPENAPI_SHA256:
            raise IntegrationError("Running OpenAPI contract differs from pinned source")
        return version

    def devices(self):
        result = []
        # Complete pagination matters: a shared policy can affect devices on later pages.
        for offset in range(0, 10000, 100):
            page = self.request("GET", "devices", params={"offset": offset, "limit": 100})
            if not isinstance(page, list):
                raise IntegrationError("Unexpected devices response")
            result.extend(page)
            if len(page) < 100:
                return result
        raise IntegrationError("Organization exceeds the isolated lab device limit")

    def policy(self):
        return self.request("GET", f"mlpolicies/{self.config.policy_id}")

    def managed_rule(self, policy):
        matches = [rule for rule in policy["rules"] if rule.get("_id") == self.config.rule_id]
        if len(matches) != 1 or not matches[0]["enabled"]:
            raise IntegrationError("Managed rule missing, duplicated or disabled")
        return matches[0]

    def preflight(self, *, reconciling=False):
        self.verify_version()
        policy = self.policy()
        self.managed_rule(policy)
        devices = self.devices()
        bound = [
            device
            for device in devices
            if label_id(device.get("policies", {}).get("multilink", {}).get("policy"))
            == self.config.policy_id
            and device["policies"]["multilink"].get("status")
            in (
                {"installed", "installing", "failed"}
                if reconciling
                else {"installed", "installing"}
            )
        ]
        if {device["_id"] for device in bound} != set(self.config.device_ids):
            raise IntegrationError("Policy scope differs from the exact device allowlist")
        for device in bound:
            if (
                device.get("isApproved") is not True
                or device.get("isConnected") is not True
                or device.get("deviceStatus", {}).get("state") != "running"
                or (not reconciling and device["policies"]["multilink"]["status"] != "installed")
            ):
                raise IntegrationError("Managed device not approved, connected and installed")
        labels = self.request("GET", "pathlabels")
        known = {entry["_id"] for entry in labels}
        if not {binding.path_label_id for binding in self.config.links.values()} <= known:
            raise IntegrationError("Configured path label missing from organization")
        return policy, bound

    def readback(self, policy):
        expected = self.managed_rule(policy)
        for device_id in self.config.device_ids:
            result = self.request("GET", f"devices/{device_id}/configuration")
            if result.get("deviceStatus") != "connected" or result.get("error"):
                raise IntegrationError("Device read-back unavailable")
            matches = [
                row["params"]
                for row in result.get("configuration", [])
                if row.get("message") == "add-multilink-policy"
                and str(row.get("params", {}).get("id")) == self.config.policy_id
            ]
            if len(matches) != 1:
                raise IntegrationError("Installed policy missing from agent configuration")
            rules = [row for row in matches[0].get("rules", []) if row.get("id") == expected["_id"]]
            if (
                len(rules) != 1
                or action_signature(rules[0]["action"]) != action_signature(expected["action"])
                or classification_signature(rules[0].get("classification"))
                != classification_signature(expected.get("classification"))
                or rules[0].get("apply-on-wan-rx") != policy.get("applyOnWan", False)
                or rules[0].get("override-default-route")
                != policy.get("overrideDefaultRoute", False)
            ):
                raise IntegrationError("Agent rule differs from requested WAN action or classifier")
        return True

    def current_path(self, policy):
        rule = self.managed_rule(policy)
        action = rule["action"]
        if action.get("order") != "priority" or not action.get("links"):
            raise IntegrationError("Managed rule must use priority order")
        first = action["links"][0]["pathlabels"]
        if (
            len(action["links"]) == 1
            and action["links"][0].get("order") == "link-quality"
            and {label_id(label) for label in first}
            == {binding.path_label_id for binding in self.config.links.values()}
            and len(first) == 3
        ):
            return None  # QBR selects dynamically; controller configuration proves no fixed WAN.
        if len(first) != 1:
            raise IntegrationError("Managed first tier must contain exactly one WAN label")
        identifier = label_id(first[0])
        for link, binding in self.config.links.items():
            if binding.path_label_id == identifier:
                return link
        raise IntegrationError("Current WAN label is outside the allowlist")

    def jobs(self, ids):
        if (
            not isinstance(ids, list)
            or not ids
            or len(ids) > 20
            or any(type(identifier) is not int or identifier < 1 for identifier in ids)
            or len(set(ids)) != len(ids)
        ):
            raise IntegrationError("Invalid queued job identifiers")
        # The pinned controller's single-job handler does not await its async callback
        # and returns {}. The filtered batch endpoint returns the actual queue records.
        jobs = self.request("GET", "jobs", params={"status": "all", "ids": ",".join(map(str, ids))})
        if (
            not isinstance(jobs, list)
            or len(jobs) != len(ids)
            or any(not isinstance(job, dict) or type(job.get("_id")) is not int for job in jobs)
            or {job["_id"] for job in jobs} != set(ids)
        ):
            raise IntegrationError("Queue response omitted or substituted expected jobs")
        return jobs

    def wait_jobs(self, response, devices):
        ids = response.get("ids", [])
        if (
            response.get("status") != "completed"
            or not isinstance(ids, list)
            or not ids
            or len(ids) > 20
            or any(type(identifier) is not int or identifier < 1 for identifier in ids)
            or len(set(ids)) != len(ids)
        ):
            raise IntegrationError("Policy update did not queue all expected jobs")
        machine_ids = {device["machineId"] for device in devices}
        deadline = time.monotonic() + self.config.job_timeout_seconds
        while time.monotonic() < deadline:
            jobs = self.jobs(ids)
            if any(job.get("type") not in machine_ids for job in jobs):
                raise IntegrationError("Job target differs from managed devices")
            if any(job.get("state") == "failed" for job in jobs):
                # A known failure can still have applied part of a batch; reconciliation required.
                raise IntegrationError("Device job failed; reconcile before another write")
            if all(job.get("state") == "complete" for job in jobs):
                if {job["type"] for job in jobs} != machine_ids:
                    raise IntegrationError("Missing device job acknowledgement")
                return ids
            time.sleep(self.config.poll_seconds)
        raise IntegrationError("Device job timeout; outcome unknown, automatic retries disabled")

    def close(self):
        self.http.close()


class ApplyJournal:
    """Durable intent before network mutation. Unknown outcomes survive restarts."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def file(self, identifier):
        # Recommendation IDs are untrusted when passed via CLI.
        digest = hashlib.sha256(identifier.encode()).hexdigest()
        return self.directory / f"{digest}.json"

    @contextmanager
    def transaction(self):
        """OS releases this lock on process death; different writers cannot race."""
        with (self.directory / "writer.lock").open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise IntegrationError("Another process owns the flexiWAN writer lock") from None
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def unresolved(self):
        return [
            path
            for path in self.directory.glob("*.json")
            if json.loads(path.read_text(encoding="utf-8"))["phase"]
            not in {"verified", "rolled_back"}
        ]

    def write(self, identifier, payload):
        target = self.file(identifier)
        temporary = target.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        if os.name != "nt":
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
