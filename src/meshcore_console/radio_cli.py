from __future__ import annotations

import argparse
import asyncio
import json
import os
import traceback
import time
from typing import Any

from meshcore_console.meshcore.session import PyMCCoreSession, load_runtime_config


def _build_parser() -> argparse.ArgumentParser:
    def add_global_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--node-name", default="uconsole-node", help="Mesh node name")
        p.add_argument(
            "--debug",
            dest="debug",
            action="store_true",
            help="Enable verbose debug logs",
        )
        p.add_argument(
            "--start-timeout",
            type=float,
            default=20.0,
            help="Fail startup if mesh node does not start within N seconds",
        )

    parser = argparse.ArgumentParser(description="Headless meshcore radio CLI")
    add_global_args(parser)

    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Check host prerequisites for pyMC_core radio access")
    add_global_args(doctor)

    listen = sub.add_parser("listen", help="Start node and print incoming events")
    add_global_args(listen)
    listen.add_argument(
        "--duration", type=int, default=0, help="Stop after N seconds (0 = until Ctrl+C)"
    )

    send = sub.add_parser("send", help="Start node and send a text message")
    add_global_args(send)
    send.add_argument("--peer", required=True, help="Peer name in meshcore contacts")
    send.add_argument("--message", required=True, help="Text body to send")

    advert = sub.add_parser("advert", help="Start node and send an advert packet")
    add_global_args(advert)
    advert.add_argument(
        "--name", default=None, help="Advert display name (defaults to --node-name)"
    )
    advert.add_argument("--lat", type=float, default=0.0, help="Advert latitude")
    advert.add_argument("--lon", type=float, default=0.0, help="Advert longitude")
    advert.add_argument(
        "--route-type",
        choices=["flood", "direct"],
        default="flood",
        help="Advert routing type",
    )

    export = sub.add_parser("export-logs", help="Export application logs for bug reports")
    export.add_argument(
        "-o",
        "--output",
        default=None,
        help="Write logs to file (default: stdout)",
    )

    return parser


def _doctor() -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("linux", os.uname().sysname == "Linux", "Expected Linux host"))
    checks.append(
        ("spidev", os.path.exists("/dev/spidev1.0"), "Expected SPI1 device /dev/spidev1.0")
    )
    checks.append(
        ("gpiochip", os.path.exists("/dev/gpiochip0"), "Expected GPIO chip /dev/gpiochip0")
    )

    try:
        import pymc_core  # noqa: F401

        checks.append(("pymc_core", True, "Python module import succeeded"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("pymc_core", False, f"Import failed: {exc}"))

    ok = True
    for name, passed, detail in checks:
        label = "OK" if passed else "FAIL"
        print(f"[{label}] {name}: {detail}")
        if not passed:
            ok = False
    return 0 if ok else 1


def _debug(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[debug] {message}", flush=True)


async def _run_listen(
    session: PyMCCoreSession, duration: int, debug: bool, start_timeout: float
) -> int:
    _debug(debug, "starting mesh node")
    await asyncio.wait_for(session.start(), timeout=start_timeout)
    _debug(debug, "mesh node started")
    print(json.dumps({"status": "started", **session.status()}))
    started = time.monotonic()
    _debug(debug, "waiting for events")
    try:
        async for event in session.listen_events():
            print(json.dumps(event, default=str))
            if duration > 0 and time.monotonic() - started >= duration:
                _debug(debug, "listen duration reached, stopping")
                break
    finally:
        _debug(debug, "stopping mesh node")
        await session.stop()
        _debug(debug, "mesh node stopped")
    return 0


async def _run_send(
    session: PyMCCoreSession,
    peer: str,
    message: str,
    debug: bool,
    start_timeout: float,
) -> int:
    _debug(debug, "starting mesh node")
    await asyncio.wait_for(session.start(), timeout=start_timeout)
    _debug(debug, "mesh node started")
    try:
        _debug(debug, f"sending message to peer={peer}")
        response: Any = await session.send_text(peer_name=peer, message=message)
        print(
            json.dumps(
                {"status": "sent", "peer": peer, "message": message, "response": str(response)}
            )
        )
    finally:
        _debug(debug, "stopping mesh node")
        await session.stop()
        _debug(debug, "mesh node stopped")
    return 0


async def _run_advert(
    session: PyMCCoreSession,
    *,
    name: str | None,
    lat: float,
    lon: float,
    route_type: str,
    debug: bool,
    start_timeout: float,
) -> int:
    _debug(debug, "starting mesh node")
    await asyncio.wait_for(session.start(), timeout=start_timeout)
    _debug(debug, "mesh node started")
    try:
        _debug(debug, f"sending advert name={name or '<node-name>'} route={route_type}")
        result = await session.send_advert(name=name, lat=lat, lon=lon, route_type=route_type)
        print(
            json.dumps(
                {
                    "status": "advert_sent",
                    "success": bool(result.get("success")),
                    "name": name or session.config.node_name,
                    "lat": lat,
                    "lon": lon,
                    "route_type": route_type,
                    "tx_metadata": result.get("tx_metadata"),
                    "dispatcher_result": result.get("dispatcher_result"),
                }
            )
        )
        if not result.get("success"):
            return 1
    finally:
        _debug(debug, "stopping mesh node")
        await session.stop()
        _debug(debug, "mesh node stopped")
    return 0


def _export_logs(output: str | None) -> int:
    from meshcore_console.meshcore.logging_setup import export_logs_to_path, export_logs_to_stdout

    if output:
        export_logs_to_path(output)
        print(f"Logs written to {output}")
    else:
        export_logs_to_stdout()
    return 0


async def _async_main(args: argparse.Namespace) -> int:
    if args.command == "export-logs":
        return _export_logs(args.output)

    _debug(args.debug, f"command={args.command} node_name={args.node_name}")
    if args.command == "doctor":
        _debug(args.debug, "running doctor checks")
        return _doctor()

    config = load_runtime_config(node_name=args.node_name)
    session = PyMCCoreSession(config, logger=lambda msg: _debug(args.debug, f"session: {msg}"))

    if args.command == "listen":
        return await _run_listen(session, args.duration, args.debug, args.start_timeout)
    if args.command == "send":
        return await _run_send(session, args.peer, args.message, args.debug, args.start_timeout)
    if args.command == "advert":
        return await _run_advert(
            session,
            name=args.name,
            lat=args.lat,
            lon=args.lon,
            route_type=args.route_type,
            debug=args.debug,
            start_timeout=args.start_timeout,
        )
    raise RuntimeError(f"Unsupported command: {args.command}")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_async_main(args))
    except TimeoutError:
        print(
            "error: startup timed out. Run with --debug to see the last startup stage; "
            "common causes are SPI pin mismatch or radio not responding."
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        if getattr(args, "debug", False):
            print(f"[debug] error: {exc}")
            traceback.print_exc()
        else:
            print(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
