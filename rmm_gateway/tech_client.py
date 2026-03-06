#!/usr/bin/env python3
"""Simple technician WebSocket client for testing the RMM gateway.

Env vars:
  RMM_GATEWAY_URL   e.g. ws://127.0.0.1:8765
  RMM_TECH_API_KEY  API key with permission rmm_connect

Usage:
  python tech_client.py --agent-id PC-01 --reason "help user" --command whoami
"""

import argparse
import asyncio
import json
import os

import websockets


def get_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tracker RMM tech test client")
    p.add_argument("--agent-id", required=True)
    p.add_argument("--reason", default="")
    p.add_argument("--command", default="whoami", help="Command key (e.g. whoami/hostname/ipconfig)")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    gateway = get_env("RMM_GATEWAY_URL").rstrip("/")
    api_key = get_env("RMM_TECH_API_KEY")

    ws_url = f"{gateway}/ws/tech/{args.agent_id}?api_key={api_key}&reason={args.reason}"

    async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
        first = await ws.recv()
        print("<-", first)

        await ws.send(json.dumps({"type": "exec", "command": args.command}))
        print("-> exec", args.command)

        while True:
            msg = await ws.recv()
            print("<-", msg)
            payload = json.loads(msg)
            if payload.get("type") == "exec_result":
                break


if __name__ == "__main__":
    asyncio.run(main())
