#!/usr/bin/env python3
"""Check server reachability and optionally publish a local HTTP service."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request


IP_SERVICES = ("https://api.ipify.org?format=json", "https://ifconfig.me/ip")


def fetch(url: str, timeout: float = 5.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "scholarsprout-deploy-check/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace").strip()


def external_ip() -> dict[str, str]:
    result: dict[str, str] = {}
    for url in IP_SERVICES:
        try:
            value = fetch(url)
            if value.startswith("{"):
                value = json.loads(value).get("ip", value)
            result[url] = value
        except Exception as exc:
            result[url] = f"ERROR: {exc}"
    return result


def local_port_status(host: str, port: int) -> dict[str, object]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        connected = sock.connect_ex((host, port)) == 0
    finally:
        sock.close()
    return {"host": host, "port": port, "open": connected}


def url_status(url: str) -> dict[str, object]:
    try:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "scholarsprout-deploy-check/1.0"})
        with urllib.request.urlopen(request, timeout=8) as response:
            return {"url": url, "ok": True, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"url": url, "ok": True, "status": exc.code}
    except Exception as exc:
        return {"url": url, "ok": False, "error": str(exc)}


def print_check(port: int, probe_urls: list[str]) -> None:
    print("=== external address ===")
    for service, value in external_ip().items():
        print(f"{service}: {value}")

    print("\n=== outbound HTTPS ===")
    for url in probe_urls:
        print(url_status(url))

    print("\n=== local service ===")
    for host in ("127.0.0.1", "0.0.0.0"):
        print(local_port_status(host, port))

    print("\n=== interpretation ===")
    print("An outbound address does not prove inbound access to this machine.")
    print("Check firewall, NAT, and school network policy from an external machine.")


def run_tunnel(port: int) -> int:
    executable = shutil.which("cloudflared")
    if not executable:
        print("cloudflared was not found; Quick Tunnel is optional and for testing only.", file=sys.stderr)
        return 2
    print("Starting a temporary tunnel. Keep this process running.")
    process = subprocess.Popen(
        [executable, "tunnel", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
    except KeyboardInterrupt:
        process.terminate()
    return process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000, help="local HTTP service port")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="show outbound IP and local port status")
    check.add_argument("--port", dest="subcommand_port", type=int)
    check.add_argument("--probe-url", action="append", default=[])
    tunnel = subparsers.add_parser("tunnel", help="publish the local port with cloudflared")
    tunnel.add_argument("--port", dest="subcommand_port", type=int)
    args = parser.parse_args()
    port = args.subcommand_port or args.port
    if args.command == "check":
        print_check(port, args.probe_url or ["https://github.com", "https://api.ipify.org"])
        return 0
    return run_tunnel(port)


if __name__ == "__main__":
    raise SystemExit(main())
