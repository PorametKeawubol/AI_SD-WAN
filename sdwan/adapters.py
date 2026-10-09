from dataclasses import dataclass


@dataclass(frozen=True)
class ApplyResult:
    verified: bool
    backend: str
    detail: str


class DryRunAdapter:
    """Simulated acknowledgement; never sends requests to network devices."""

    backend = "dry_run"

    def apply(self, source: str | None, target: str, recommendation_id: str) -> ApplyResult:
        return ApplyResult(True, self.backend, f"Simulated path {source} -> {target}")


class FlexiWANAdapter:
    """Apply one dedicated policy, verify jobs AND agent configuration, journal intent."""

    def __init__(self, config=None, client=None):
        from .flexiwan import ApplyJournal, FlexiManageClient

        self.config = config
        self.backend = "flexiwan" if config else "flexiwan_unconfigured"
        self.client = (client or FlexiManageClient(config)) if config else None
        self.journal = ApplyJournal(config.journal_dir) if config else None

    def apply(self, source: str | None, target: str, recommendation_id: str) -> ApplyResult:
        if self.journal is None:
            return self._apply(source, target, recommendation_id)
        with self.journal.transaction():
            return self._apply(source, target, recommendation_id)

    def _apply(self, source, target, recommendation_id):
        from .flexiwan import IntegrationError, policy_body

        if self.client is None:
            raise IntegrationError(
                "flexiWAN lab integration is unconfigured; no network request was sent"
            )
        if target not in self.config.links:
            raise IntegrationError("Target outside WAN allowlist")
        if self.journal.unresolved():
            raise IntegrationError("Unresolved apply exists; run flexiwan reconcile before writing")
        if self.journal.file(recommendation_id).exists():
            raise IntegrationError("Recommendation already submitted; do not replay")
        original, devices = self.client.preflight()
        self.client.readback(original)
        if self.client.current_path(original) != source:
            raise IntegrationError("Source WAN changed; synchronize controller state first")
        updated = policy_body(original)
        rule = self.client.managed_rule(updated)
        order = [target, *[link for link in self.config.links if link != target]]
        rule["action"] = {
            "order": "priority",
            "fallback": "drop",
            "links": [
                {
                    "order": "priority",
                    "pathlabels": [{"_id": self.config.links[link].path_label_id}],
                }
                for link in order
            ],
        }
        return self._commit_update(original, updated, devices, recommendation_id, source, target)

    def apply_experiment_policy(self, updated, recommendation_id):
        """Install the dedicated lab classifier/baseline through the same verified write path."""
        from .flexiwan import IntegrationError, policy_signature

        with self.journal.transaction():
            if self.journal.unresolved() or self.journal.file(recommendation_id).exists():
                raise IntegrationError("Unresolved or replayed experiment update")
            original, devices = self.client.preflight()
            self.client.readback(original)
            before, after = policy_signature(original), policy_signature(updated)
            for policy in (before, after):
                rule = self.client.managed_rule(policy)
                rule.pop("classification", None)
                rule.pop("action", None)
            if before != after:
                raise IntegrationError("Experiment update changed unmanaged policy fields")
            source = self.client.current_path(original)
            target = self.client.current_path(updated)
            return self._commit_update(
                original, updated, devices, recommendation_id, source, target
            )

    def _commit_update(self, original, updated, devices, recommendation_id, source, target):
        import hashlib

        from .flexiwan import policy_body

        entry = {
            "id": recommendation_id,
            "phase": "intent",
            "source": source,
            "target": target,
            "original": policy_body(original),
            "requested": updated,
            "scope": hashlib.sha256(self.config.model_dump_json().encode()).hexdigest(),
        }
        self.journal.write(recommendation_id, entry)
        # No transport retries: after PUT timeout the remote write may have succeeded.
        response = self.client.request("PUT", f"mlpolicies/{self.config.policy_id}", body=updated)
        entry.update(phase="queued", job_ids=response.get("ids", []))
        self.journal.write(recommendation_id, entry)
        self.client.wait_jobs(response, devices)
        self.client.readback(updated)
        entry["phase"] = "verified"
        self.journal.write(recommendation_id, entry)
        return ApplyResult(
            True, self.backend, f"Jobs and agent policy verified on {len(devices)} device(s)"
        )

    def reconcile(self, *, rollback=False):
        with self.journal.transaction():
            return self._reconcile(rollback=rollback)

    def _reconcile(self, *, rollback=False):
        """Resolve an uncertain write by read-back; explicit rollback uses saved snapshot."""
        import hashlib
        import json

        from .flexiwan import IntegrationError, policy_signature

        paths = self.journal.unresolved()
        if len(paths) != 1:
            raise IntegrationError("Reconciliation requires exactly one unresolved apply")
        entry = json.loads(paths[0].read_text(encoding="utf-8"))
        if entry.get("scope") != hashlib.sha256(self.config.model_dump_json().encode()).hexdigest():
            raise IntegrationError("Journal scope differs from current configuration")
        remote, devices = self.client.preflight(reconciling=True)
        # Never race an active queue, nor overwrite a concurrent administrator change.
        job_ids = entry.get("job_ids", [])
        if job_ids:
            jobs = self.client.jobs(job_ids)
            if {job.get("type") for job in jobs} != {device["machineId"] for device in devices}:
                raise IntegrationError("Job target differs from managed devices; rollback deferred")
            if any(job.get("state") not in {"complete", "failed"} for job in jobs):
                raise IntegrationError("Job is still active or unknown; rollback deferred")
        if policy_signature(remote) != policy_signature(entry["requested"]):
            raise IntegrationError("Remote policy changed; manual conflict resolution required")
        if rollback:
            if not entry.get("job_ids"):
                raise IntegrationError(
                    "Lost submission acknowledgement: inspect server queue before rollback"
                )
            response = self.client.request(
                "PUT", f"mlpolicies/{self.config.policy_id}", body=entry["original"]
            )
            # Persist rollback IDs immediately, so restart does not replay an unknown rollback.
            entry.update(
                phase="rollback_queued",
                job_ids=response.get("ids", []),
                requested=entry["original"],
            )
            self.journal.write(entry["id"], entry)
            self.client.wait_jobs(response, devices)
            self.client.readback(entry["original"])
            entry["phase"] = "rolled_back"
        else:
            self.client.readback(entry["requested"])
            entry["phase"] = "verified"
        self.journal.write(entry["id"], entry)
        return self.client.current_path(entry["original"] if rollback else entry["requested"])
