import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from pydantic import ValidationError

from .adapters import DryRunAdapter
from .domain import LINKS, PROFILES, WINDOW, Telemetry
from .features import resample_live_history
from .model import Forecaster
from .policy import Controller
from .storage import Store


class Conflict(ValueError):
    pass


class Service:
    """Serialized lab control loop. Run exactly one ASGI worker."""

    def __init__(
        self,
        database_url=None,
        model_dir="artifacts/model",
        adapter=None,
        *,
        forecast_sampling="strict",
        stream="live",
        store=None,
    ):
        if forecast_sampling not in {"strict", "causal_linear_5s"}:
            raise ValueError("Unknown forecast sampling contract")
        self.forecast_sampling = forecast_sampling
        history_steps = WINDOW + 1 if forecast_sampling == "causal_linear_5s" else WINDOW
        self.lock = threading.RLock()
        self.store = store if store is not None else Store(database_url, stream=stream)
        self.adapter = adapter or DryRunAdapter()
        self.apply_executor = (
            ThreadPoolExecutor(max_workers=1) if self.adapter.backend == "flexiwan" else None
        )
        self.inflight = None
        self.mode = self.store.get("mode", "manual")
        self.profile = self.store.get("profile", "voice")
        self.controller = Controller(PROFILES[self.profile])
        state = self.store.get("route", {})
        self.controller.current = state.get("current", "wan1")
        self.controller.last_switch = state.get("last_switch", float("-inf"))
        self.histories = {
            link: deque(
                (Telemetry.model_validate(row) for row in self.store.history(link, history_steps)),
                maxlen=history_steps,
            )
            for link in LINKS
        }
        self.latest = {link: history[-1] for link, history in self.histories.items() if history}
        self.pending = None
        self.last_decision = None
        self.last_tick = None
        self.version = 0
        self._telemetry_version = 0
        self._forecast_cache_key = None
        self._forecast_cache_model = None
        self._forecast_cache = {}
        self.model_error = None
        self.model = None
        try:
            self.model = Forecaster(model_dir)
        except (OSError, ValueError, KeyError) as error:
            self.model_error = f"Model unavailable: {type(error).__name__}; reactive fallback"
        # Outstanding recommendations are deliberately invalidated on restart.
        self.store.log(
            time.time(),
            "startup",
            {"backend": self.adapter.backend, "mode": self.mode, "model_error": self.model_error},
        )

    def ingest(self, rows, now=None):
        now = time.time() if now is None else now
        with self.lock:
            accepted, rejected = [], []
            batch_links = set()
            for index, row in enumerate(rows):
                try:
                    sample = Telemetry.model_validate(row)
                    if sample.link_id in batch_links:
                        raise ValueError("duplicate link in batch")
                    batch_links.add(sample.link_id)
                    if not sample.fresh(now):
                        raise ValueError("stale/future timestamp")
                    previous = self.latest.get(sample.link_id)
                    if previous and sample.timestamp <= previous.timestamp:
                        raise ValueError("replayed/out-of-order timestamp")
                    accepted.append(sample)
                except (ValidationError, ValueError) as error:
                    # Do not log untrusted raw payloads, NaN values, or secrets.
                    rejected.append({"index": index, "reason": str(error).split("\n")[0]})
            if accepted:
                self.store.ingest(accepted)
                for sample in accepted:
                    self.latest[sample.link_id] = sample
                    self.histories[sample.link_id].append(sample)
                self.version += 1
                self._telemetry_version += 1
            if rejected:
                self.store.log(now, "telemetry_rejected", {"rejected": rejected})
            return {"accepted": len(accepted), "rejected": rejected}

    def forecasts(self):
        # A forecast depends on accepted measurements, the loaded model and the
        # sampling contract, not wall time, application profile or control mode.
        # Keep the cache behind the control lock so concurrent readers cannot
        # repeat inference or publish predictions for different telemetry frames.
        with self.lock:
            model = self.model
            key = (self._telemetry_version, self.forecast_sampling)
            if key == self._forecast_cache_key and model is self._forecast_cache_model:
                return deepcopy(self._forecast_cache)
            try:
                predictions = {}
                if model is not None:
                    for link, history in self.histories.items():
                        samples = list(history)
                        if self.forecast_sampling == "causal_linear_5s":
                            samples = resample_live_history(samples)
                            if samples is None:
                                continue
                        values = model.predict(samples)
                        if values is not None:
                            predictions[link] = values
                # Cache empty valid results too: incomplete history remains
                # incomplete until another measurement is accepted.
                self._forecast_cache = deepcopy(predictions)
                self._forecast_cache_key = key
                self._forecast_cache_model = model
                return deepcopy(self._forecast_cache)
            except Exception as error:  # noqa: BLE001 - prediction must fall back rather than break routing
                self.model_error = f"Prediction failed: {type(error).__name__}; reactive fallback"
                # Do not cache failures; preserve retry after transient model
                # errors, even when the telemetry frame has not changed.
                return {}

    def tick(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            self._finish_inflight(now)
            if getattr(self, "observing", False):
                self.pending = None
                self.last_decision = {
                    "action": "observe"
                    if any(s.up and s.fresh(now) for s in self.latest.values())
                    else "no_path",
                    "reason": "Native QBR observation; AI policy writes disabled",
                }
                return self.status(now)
            # API calls cannot manufacture three confirmations on one telemetry frame.
            if self.last_tick == (now, self.version):
                return self.status(now)
            if self.last_tick and self.version == self.last_tick[1] and now - self.last_tick[0] < 5:
                return self.status(now)
            self.last_tick = (now, self.version)
            decision = self.controller.propose(self.latest, now, self.forecasts())
            self.last_decision = decision.to_dict()
            if decision.action != "switch":
                if self.pending:
                    self.store.log(now, "recommendation_invalidated", {"id": self.pending["id"]})
                    self.pending = None
            elif not self.inflight:
                if (
                    not self.pending
                    or self.pending["target"] != decision.target
                    or now > self.pending["expires_at"]
                ):
                    self.pending = {
                        "id": uuid.uuid4().hex,
                        "source": decision.source,
                        "target": decision.target,
                        "created_at": now,
                        "expires_at": now + 30,
                        "reason": decision.reason,
                    }
                    self.store.log(now, "recommendation", self.pending)
                if self.mode == "auto":
                    self._apply(now)
            self.store.log(
                now,
                "decision",
                {
                    **self.last_decision,
                    "mode": self.mode,
                    "profile": self.profile,
                    "model_error": self.model_error,
                },
            )
            return self.status(now)

    def _apply(self, now):
        if self.inflight:
            return False
        recommendation = dict(self.pending)
        self.store.log(now, "apply_intent", recommendation)
        if self.apply_executor:
            self.inflight = {
                "recommendation": recommendation,
                "started_at": now,
                "future": self.apply_executor.submit(self._execute_adapter, recommendation),
            }
            self.pending = None
            self.store.log(
                now,
                "apply_started",
                {"id": recommendation["id"], "target": recommendation["target"]},
            )
            return True
        started = time.monotonic()
        try:
            result = self.adapter.apply(
                recommendation["source"], recommendation["target"], recommendation["id"]
            )
            if not result.verified:
                raise RuntimeError("adapter did not verify device read-back")
        except Exception as error:  # noqa: BLE001 - failed adapter must never advance confirmed route
            self.store.log(
                now,
                "apply_failed",
                {"id": recommendation["id"], "error_type": type(error).__name__},
            )
            self.pending = None
            return False
        duration = time.monotonic() - started
        completed_at = now + duration
        self.controller.acknowledge(recommendation["target"], completed_at)
        self.store.put("route", {"current": self.controller.current, "last_switch": completed_at})
        self.store.log(
            completed_at,
            "applied",
            {
                **recommendation,
                "backend": result.backend,
                "detail": result.detail,
                "recommendation_to_apply_seconds": completed_at - recommendation["created_at"],
                "adapter_duration_seconds": duration,
            },
        )
        self.pending = None
        return True

    def _execute_adapter(self, recommendation):
        # Network job polling runs outside Service.lock. Telemetry/UI stay responsive.
        started = time.monotonic()
        try:
            result = self.adapter.apply(
                recommendation["source"], recommendation["target"], recommendation["id"]
            )
            if not result.verified:
                raise RuntimeError("adapter did not verify device read-back")
            return {
                "result": result,
                "completed_at": time.time(),
                "duration": time.monotonic() - started,
            }
        except Exception as error:  # noqa: BLE001 - safe public error, no credentials
            return {
                "error_type": type(error).__name__,
                "completed_at": time.time(),
                "duration": time.monotonic() - started,
            }

    def _finish_inflight(self, now):
        if not self.inflight or not self.inflight["future"].done():
            return
        record = self.inflight
        self.inflight = None
        outcome = record["future"].result()
        recommendation = record["recommendation"]
        completed_at = outcome["completed_at"]
        if "error_type" in outcome:
            self.store.log(
                completed_at,
                "apply_failed",
                {"id": recommendation["id"], "error_type": outcome["error_type"]},
            )
            return
        self.controller.acknowledge(recommendation["target"], completed_at)
        self.store.put("route", {"current": self.controller.current, "last_switch": completed_at})
        self.store.log(
            completed_at,
            "applied",
            {
                **recommendation,
                "backend": outcome["result"].backend,
                "detail": outcome["result"].detail,
                "adapter_duration_seconds": outcome["duration"],
                "recommendation_to_apply_seconds": completed_at - recommendation["created_at"],
            },
        )
        self.controller.reset_candidate()

    def close(self):
        if self.apply_executor:
            self.apply_executor.shutdown(wait=True, cancel_futures=False)
            self._finish_inflight(time.time())
        client = getattr(self.adapter, "client", None)
        if client:
            client.close()

    def review(self, recommendation_id, approve, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if self.mode != "manual" or not self.pending or self.pending["id"] != recommendation_id:
                raise Conflict("recommendation is no longer pending in manual mode")
            if now > self.pending["expires_at"]:
                self.pending = None
                raise Conflict("recommendation expired")
            if not approve:
                self.store.log(
                    now,
                    "rejected",
                    {
                        "id": recommendation_id,
                        "approval_response_seconds": now - self.pending["created_at"],
                    },
                )
                self.pending = None
                self.controller.reset_candidate()
                return self.status(now)
            decision = self.controller.propose(self.latest, now, self.forecasts())
            if (
                decision.action != "switch"
                or decision.target != self.pending["target"]
                or self.controller.current != self.pending["source"]
            ):
                self.pending = None
                raise Conflict("network state changed; recommendation must be regenerated")
            self.store.log(
                now,
                "approved",
                {
                    "id": recommendation_id,
                    "approval_response_seconds": now - self.pending["created_at"],
                },
            )
            if not self._apply(now):
                raise Conflict("apply failed; confirmed route has not advanced")
            return self.status(now)

    def configure(self, mode=None, profile=None, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if getattr(self, "observing", False) and mode == "auto":
                raise Conflict("Native QBR observation cannot enable AI writes")
            if mode is not None:
                if mode not in {"auto", "manual"}:
                    raise ValueError("invalid mode")
                self.mode = mode
                self.store.put("mode", mode)
            if profile is not None:
                if profile not in PROFILES:
                    raise ValueError("invalid profile")
                self.profile = profile
                self.controller.profile = PROFILES[profile]
                self.store.put("profile", profile)
            self.pending = None
            self.controller.reset_candidate()
            self.version += 1
            self.store.log(now, "configuration", {"mode": self.mode, "profile": self.profile})
            return self.status(now)

    def status(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            available = any(s.up and s.fresh(now) for s in self.latest.values())
            return {
                "mode": self.mode,
                "profile": self.profile,
                "backend": self.adapter.backend,
                "route_evidence": "agent_configuration"
                if self.adapter.backend.startswith("flexiwan")
                else "simulation",
                "observing_native_qbr": getattr(self, "observing", False),
                "confirmed_path": self.controller.current,
                "network_available": available,
                "model_loaded": self.model is not None,
                "model_error": self.model_error,
                "forecast_sampling": self.forecast_sampling,
                "telemetry": {
                    k: {
                        **v.model_dump(),
                        "fresh": v.fresh(now),
                        "age_seconds": max(0, now - v.timestamp),
                    }
                    for k, v in self.latest.items()
                },
                "pending": self.pending
                if self.pending and now <= self.pending["expires_at"]
                else None,
                "apply_inflight": {
                    "id": self.inflight["recommendation"]["id"],
                    "target": self.inflight["recommendation"]["target"],
                    "started_at": self.inflight["started_at"],
                }
                if self.inflight
                else None,
                "last_decision": self.last_decision,
            }
