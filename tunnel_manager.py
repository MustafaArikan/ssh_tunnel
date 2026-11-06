#!/usr/bin/env python3
"""Auto-reconnecting reverse SSH tunnels.

Every connection opens a reverse tunnel to a remote host, exposing local
ports on the remote server. Connections are declared in .env (see README)
and supervised independently: each one runs its own reconnect loop in its
own thread, and its SSH child is restarted when it exits or once it has
been up longer than RECONNECT_INTERVAL hours.
"""
import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / ".env"
DEFAULT_INTERVAL = 1.0
SHUTDOWN = threading.Event()
CHILDREN = set()
CHILDREN_LOCK = threading.Lock()


def parse_config(text):
    """Parse a sectioned .env file into {section: {KEY: VALUE}}.

    Keys appearing before the first [section] land in a "default" section,
    so the original flat .env layout keeps working unchanged.
    """
    sections = {}
    current = "default"
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        sections.setdefault(current, {})[key.strip()] = value.strip()
    return sections


def parse_bool(value, default=False):
    if value is None:
        return default
    return value.strip().lower() in ("1", "yes", "true", "on")


def parse_forwards(spec, name):
    forwards = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"bad FORWARDS entry {chunk!r}, expected REMOTE:LOCAL")
        remote, local = chunk.split(":", 1)
        forwards.append((int(remote), int(local)))
    if not forwards:
        raise ValueError("FORWARDS is empty")
    return forwards


def terminate(proc):
    """Stop an ssh child, escalating to SIGKILL if it will not go away."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


class Connection:
    def __init__(self, name, host, ssh_port, forwards, interval_hours,
                 identity_file=None, allow_password=False):
        self.name = name
        self.host = host
        self.ssh_port = ssh_port
        self.forwards = forwards
        self.interval_hours = interval_hours
        self.identity_file = identity_file
        self.allow_password = allow_password

    @classmethod
    def from_values(cls, name, values):
        """Build a connection from one config section, or None if it is not one."""
        host = values.get("HOST") or values.get("REMOTE_HOST")
        if not host or host == "user@host":
            return None

        ssh_port = int(values.get("SSH_PORT") or values.get("PORT") or 22)

        if values.get("FORWARDS"):
            forwards = parse_forwards(values["FORWARDS"], name)
        else:
            # Legacy flat layout: REMOTE_PORT + LOCAL_PORT.
            remote = values.get("REMOTE_PORT")
            if not remote:
                return None
            forwards = [(int(remote), int(values.get("LOCAL_PORT", 22)))]

        return cls(
            name=name,
            host=host,
            ssh_port=ssh_port,
            forwards=forwards,
            interval_hours=float(values.get("RECONNECT_INTERVAL", DEFAULT_INTERVAL)),
            identity_file=values.get("IDENTITY_FILE") or values.get("KEY"),
            allow_password=parse_bool(values.get("ALLOW_PASSWORD")),
        )

    def command(self):
        args = [
            "ssh", "-N",
            "-p", str(self.ssh_port),
            "-o", "ServerAliveInterval=60",
            "-o", "ServerAliveCountMax=3",
            "-o", "ExitOnForwardFailure=yes",
        ]
        if not self.allow_password:
            # Unattended: fail fast instead of blocking on a password prompt.
            args += ["-o", "BatchMode=yes"]
        if self.identity_file:
            args += ["-i", os.path.expanduser(self.identity_file)]
        for remote, local in self.forwards:
            args += ["-R", f"{remote}:localhost:{local}"]
        args.append(self.host)
        return args

    def describe(self):
        fwd = ", ".join(f"{r}->localhost:{l}" for r, l in self.forwards)
        return f"{self.host}:{self.ssh_port}  [{fwd}]"

    def run(self):
        cmd = self.command()
        interval_seconds = self.interval_hours * 3600

        while not SHUTDOWN.is_set():
            print(f"[{self.name}] starting: {' '.join(cmd)}", flush=True)
            proc = subprocess.Popen(cmd)
            with CHILDREN_LOCK:
                CHILDREN.add(proc)
            try:
                proc.wait(timeout=interval_seconds)
                print(f"[{self.name}] ssh exited (code {proc.returncode}); reconnecting",
                      flush=True)
            except subprocess.TimeoutExpired:
                print(f"[{self.name}] {self.interval_hours}h limit reached; recycling",
                      flush=True)
                terminate(proc)
            finally:
                with CHILDREN_LOCK:
                    CHILDREN.discard(proc)

            if SHUTDOWN.wait(5):
                break


def handle_signal(signum, _frame):
    if SHUTDOWN.is_set():
        return
    SHUTDOWN.set()
    print(f"\nReceived signal {signum}, shutting down tunnels...", flush=True)
    with CHILDREN_LOCK:
        procs = list(CHILDREN)
    for proc in procs:
        terminate(proc)


def build_connections(args):
    if args.host:
        if not args.remote_port:
            raise ValueError("--host requires --remote-port")
        return [Connection("adhoc", args.host, 22,
                           [(args.remote_port, args.local_port)], args.interval)]

    config_path = Path(os.path.expanduser(args.config))
    if not config_path.exists():
        raise FileNotFoundError(f"config not found: {config_path}")

    connections = []
    for name, values in parse_config(config_path.read_text()).items():
        conn = Connection.from_values(name, values)
        if conn:
            connections.append(conn)
    return connections


def main():
    parser = argparse.ArgumentParser(description="Auto-reconnecting reverse SSH tunnels")
    parser.add_argument("--config", default=str(CONFIG_PATH),
                        help="Path to sectioned .env file")
    parser.add_argument("--only", action="append", metavar="NAME",
                        help="Supervise only this connection (repeatable)")
    parser.add_argument("--list", action="store_true",
                        help="List configured connections and exit")
    parser.add_argument("--host", help="Ad-hoc single tunnel: remote host (user@ip)")
    parser.add_argument("--remote-port", type=int, help="Ad-hoc: remote port to forward")
    parser.add_argument("--local-port", type=int, default=22, help="Ad-hoc: local port")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="Ad-hoc: reconnect interval in hours")
    args = parser.parse_args()

    try:
        connections = build_connections(args)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.only:
        wanted = set(args.only)
        missing = wanted - {c.name for c in connections}
        if missing:
            print(f"Error: no such connection(s): {', '.join(sorted(missing))}",
                  file=sys.stderr)
            return 1
        connections = [c for c in connections if c.name in wanted]

    if not connections:
        print("Error: no connections configured (see .env and README)", file=sys.stderr)
        return 1

    if args.list:
        for conn in connections:
            print(f"{conn.name}: {conn.describe()}")
        return 0

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    threads = []
    for conn in connections:
        print(f"[{conn.name}] configured -> {conn.describe()}", flush=True)
        thread = threading.Thread(target=conn.run, name=conn.name, daemon=True)
        thread.start()
        threads.append(thread)

    print(f"Supervising {len(connections)} connection(s). Ctrl-C to stop.", flush=True)
    while any(t.is_alive() for t in threads):
        time.sleep(1)

    for thread in threads:
        thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
