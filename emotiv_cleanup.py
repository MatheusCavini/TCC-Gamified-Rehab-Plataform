#!/usr/bin/env python3
"""Standalone Cortex session cleanup / quota recovery tool.

Run this by itself (no ROS needed) to fix the "-32019 Session limit on this
device has been reached" error. It will:

  1. Connect to the local Cortex service and authorize.
  2. Print your license's current localQuota / sessionCount.
  3. List every session Cortex still has open for your app (querySessions)
     and close each one (updateSession status="close") to release quota
     that stale/crashed runs left dangling.
  4. Optionally top the quota back up with a fresh debit.
  5. Print the license info again so you can confirm it actually changed.

Usage:
    pip install websocket-client
    python3 cortex_session_cleanup.py \
        --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET \
        --debit 20

Make sure EMOTIV Launcher (and its Cortex service) is running first.
"""

import ssl
import json
import argparse

import websocket  # pip install websocket-client

CORTEX_URL = "wss://localhost:6868"


class CortexConnection:
    def __init__(self, url):
        self.ws = websocket.create_connection(
            url, sslopt={"cert_reqs": ssl.CERT_NONE}, timeout=10.0
        )
        self.request_id = 0

    def rpc(self, method, params):
        self.request_id += 1
        request_id = self.request_id
        self.ws.send(json.dumps({
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
        }))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") != request_id:
                # Unrelated stream/warning message; ignore and keep waiting.
                continue
            if "error" in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            return message.get("result", {})

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def print_license_info(client, token, label):
    info = client.rpc("getLicenseInfo", {"cortexToken": token})
    license_ = info.get("license", {})
    print(f"\n--- License info ({label}) ---")
    print(f"  localQuota:   {license_.get('localQuota')}")
    print(f"  sessionCount: {license_.get('sessionCount')}")
    print(f"  totalDebit:   {license_.get('totalDebit')}")
    print(f"  maxDebit:     {license_.get('maxDebit')}")
    print(f"  expired:      {license_.get('expired')}")
    print("-------------------------------\n")


def main():
    parser = argparse.ArgumentParser(description="Cortex session cleanup / quota recovery")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--license", default="", help="Optional license id")
    parser.add_argument(
        "--debit", type=int, default=0,
        help="Optional number of sessions to top up the local quota by, after cleanup",
    )
    args = parser.parse_args()

    client = CortexConnection(CORTEX_URL)

    print("Requesting access (approve in EMOTIV Launcher if prompted)...")
    client.rpc("requestAccess", {
        "clientId": args.client_id, "clientSecret": args.client_secret,
    })

    print("Authorizing (no debit yet, just to inspect current state)...")
    auth_params = {"clientId": args.client_id, "clientSecret": args.client_secret}
    if args.license:
        auth_params["license"] = args.license
    token = client.rpc("authorize", auth_params)["cortexToken"]

    print_license_info(client, token, "before cleanup")

    print("Querying open sessions for this app...")
    sessions = client.rpc("querySessions", {"cortexToken": token})
    if not sessions:
        print("No open sessions found.")
    else:
        print(f"Found {len(sessions)} open session(s). Closing each...")
        for session in sessions:
            session_id = session.get("id")
            status = session.get("status")
            headset = session.get("headset", {}).get("id", "?")
            print(f"  - Closing session {session_id} (status={status}, headset={headset})")
            try:
                client.rpc("updateSession", {
                    "cortexToken": token, "session": session_id, "status": "close",
                })
            except Exception as error:
                print(f"    Failed to close: {error}")

    print_license_info(client, token, "after cleanup, before debit")

    if args.debit:
        print(f"Re-authorizing with debit={args.debit} to top up local quota...")
        auth_params["debit"] = args.debit
        token = client.rpc("authorize", auth_params)["cortexToken"]
        print_license_info(client, token, "after debit")
    else:
        print("No --debit given; skipping quota top-up. Pass --debit N if localQuota is still 0.")

    client.close()
    print("Done.")


if __name__ == "__main__":
    main()