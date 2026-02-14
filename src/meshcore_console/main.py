from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import traceback


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="meshcore-console",
        description="MeshCore console — GUI and headless CLI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=importlib.metadata.version("meshcore-uconsole"),
    )

    sub = parser.add_subparsers(dest="command")

    from meshcore_console.radio_cli import register_subcommands

    register_subcommands(sub)

    args = parser.parse_args()

    if args.command is None:
        from meshcore_console.app import run

        return run()

    from meshcore_console.radio_cli import _async_main

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
