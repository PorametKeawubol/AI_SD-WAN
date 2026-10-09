import argparse
import json
import os
import time
from pathlib import Path


def main():
    from .scenarios import SUITES, manifest, scenario, suite_cases

    parser = argparse.ArgumentParser(description="Three-WAN AI routing lab")
    commands = parser.add_subparsers(dest="command", required=True)
    train_parser = commands.add_parser("train")
    train_parser.add_argument("--output", default="artifacts/model")
    train_parser.add_argument("--episodes", type=int, default=60)
    eval_parser = commands.add_parser("eval")
    eval_parser.add_argument("--model", default="artifacts/model")
    eval_parser.add_argument("--output", default="reports/latest")
    eval_parser.add_argument("--repetitions", type=int, default=30)
    eval_parser.add_argument("--seed-base", type=int)
    eval_parser.add_argument("--suite", choices=SUITES, default="balanced")
    serve_parser = commands.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--stream", default="live")
    serve_parser.add_argument("--tls-cert", default=os.getenv("SDWAN_TLS_CERT"))
    serve_parser.add_argument("--tls-key", default=os.getenv("SDWAN_TLS_KEY"))
    serve_parser.add_argument("--flexiwan-config")
    serve_parser.add_argument("--experimental-synthetic-forecast", action="store_true")
    serve_parser.add_argument("--observe-native-qbr", action="store_true")
    flexiwan_parser = commands.add_parser("flexiwan")
    flexiwan_parser.add_argument("action", choices=["preflight", "reconcile", "rollback"])
    flexiwan_parser.add_argument("--config", required=True)
    replay_parser = commands.add_parser("replay")
    replay_parser.add_argument("--scenario", default="S12")
    replay_parser.add_argument("--url", default="https://localhost:8000")
    replay_parser.add_argument("--ca-file", default=os.getenv("SDWAN_CA_FILE"))
    replay_parser.add_argument("--seed", type=int, default=80000)
    replay_parser.add_argument("--ticks", type=int, default=150)
    scenarios_parser = commands.add_parser("scenarios")
    scenarios_parser.add_argument("--suite", choices=SUITES, default="balanced")
    scenarios_parser.add_argument("--json", action="store_true")
    report_parser = commands.add_parser("report")
    report_parser.add_argument("--output", default="reports/latest")
    args = parser.parse_args()
    if args.command == "train":
        from .model import train

        print(json.dumps(train(args.output, train_episodes=args.episodes), indent=2))
    elif args.command == "eval":
        from .evaluation import evaluate

        seed_base = (
            args.seed_base
            if args.seed_base is not None
            else (70000 if args.suite == "balanced" else 60000)
        )
        report = evaluate(args.model, args.output, args.repetitions, seed_base, suite=args.suite)
        print(json.dumps(report["counts"], indent=2))
        print(f"Evidence saved to {Path(args.output).resolve()}")
    elif args.command == "serve":
        if not args.tls_cert or not args.tls_key or not os.getenv("SDWAN_API_TOKEN"):
            parser.error("Serve requires SDWAN_API_TOKEN and TLS certificate/key")
        if (
            args.observe_native_qbr or args.experimental_synthetic_forecast
        ) and not args.flexiwan_config:
            parser.error("Live experiment flags require --flexiwan-config")
        if args.observe_native_qbr and args.experimental_synthetic_forecast:
            parser.error("Native QBR observation cannot enable the experimental AI forecast")
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.getenv("SDWAN_API_TOKEN"):
            parser.error("SDWAN_API_TOKEN is required when binding beyond localhost")
        import uvicorn

        from .api import create_app
        from .collector import live_service

        service = (
            live_service(
                args.flexiwan_config,
                stream=args.stream,
                experimental_forecast=args.experimental_synthetic_forecast,
                observe_native_qbr=args.observe_native_qbr,
            )
            if args.flexiwan_config
            else None
        )
        if not args.flexiwan_config:
            from .service import Service

            service = Service(stream=args.stream)
        uvicorn.run(
            create_app(service),
            host=args.host,
            port=args.port,
            workers=1,
            ssl_certfile=args.tls_cert,
            ssl_keyfile=args.tls_key,
        )
    elif args.command == "flexiwan":
        from .adapters import FlexiWANAdapter
        from .flexiwan import FlexiWANConfig

        adapter = FlexiWANAdapter(FlexiWANConfig.load(args.config))
        if args.action == "preflight":
            policy, devices = adapter.client.preflight()
            adapter.client.readback(policy)
            print(
                json.dumps(
                    {
                        "backend": adapter.backend,
                        "devices": len(devices),
                        "configured_path": adapter.client.current_path(policy),
                        "unresolved_applies": len(adapter.journal.unresolved()),
                    }
                )
            )
        else:
            print(
                json.dumps(
                    {"reconciled_path": adapter.reconcile(rollback=args.action == "rollback")}
                )
            )
    elif args.command == "replay":
        import httpx

        from .simulator import trace

        try:
            case = scenario(args.scenario)
        except ValueError:
            parser.error("unknown scenario; run sdwan scenarios")
        frames = trace(case, args.seed, args.ticks)
        token = os.getenv("SDWAN_API_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        import ssl

        with httpx.Client(
            base_url=args.url,
            headers=headers,
            timeout=15,
            verify=ssl.create_default_context(cafile=args.ca_file),
        ) as client:
            health = client.get("/health")
            health.raise_for_status()
            if health.json().get("backend") != "dry_run":
                parser.error("Synthetic replay is permitted only on a dry_run backend")
            for tick, frame in enumerate(frames):
                now = time.time()
                rows = [{**row, "timestamp": now} for row in frame.observed]
                response = client.post("/api/telemetry", json={"samples": rows})
                response.raise_for_status()
                state = response.json()
                print(
                    f"tick={tick:03d} event={frame.event} route={state['state']['confirmed_path']} "
                    f"pending={state['state']['pending'] is not None}",
                    flush=True,
                )
                if tick < len(frames) - 1:
                    time.sleep(max(0, 5 - (time.time() - now)))
    elif args.command == "scenarios":
        if args.json:
            print(json.dumps(manifest(args.suite), indent=2))
        else:
            for case in suite_cases(args.suite):
                targets = ",".join(case.affected_links) or "all up"
                print(f"{case.id} [{case.group}; {targets}]: {case.name}")
    elif args.command == "report":
        from .reporting import postprocess

        postprocess(args.output)
        print(f"Regenerated report and plot from saved evidence in {args.output}")


if __name__ == "__main__":
    main()
