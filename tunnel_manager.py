#!/usr/bin/env python3
import subprocess
import time
import argparse
import sys
import os
from pathlib import Path

def load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

def run_tunnel(remote_host, remote_port, local_port, interval_hours):
    cmd = [
        "ssh", "-N", "-R", f"{remote_port}:localhost:{local_port}",
        "-o", "ServerAliveInterval=60",
        "-o", "ExitOnForwardFailure=yes",
        remote_host
    ]
    
    interval_seconds = interval_hours * 3600
    
    while True:
        print(f"Starting SSH tunnel to {remote_host} ({remote_port} -> {local_port})...")
        process = subprocess.Popen(cmd)
        
        try:
            # Wait for the process to finish or for the timeout
            process.wait(timeout=interval_seconds)
            print("SSH process exited. Restarting...")
        except subprocess.TimeoutExpired:
            print(f"Reached {interval_hours} hour(s) limit. Reconnecting...")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        
        time.sleep(5)  # Short delay before reconnecting

if __name__ == "__main__":
    load_env()
    
    parser = argparse.ArgumentParser(description="Auto-reconnecting SSH Tunnel")
    parser.add_argument("--host", default=os.getenv("REMOTE_HOST", "user@host"), help="Remote host (user@ip)")
    parser.add_argument("--remote-port", type=int, default=int(os.getenv("REMOTE_PORT", 22224)), help="Remote port to forward")
    parser.add_argument("--local-port", type=int, default=int(os.getenv("LOCAL_PORT", 22)), help="Local port to forward")
    parser.add_argument("--interval", type=float, default=float(os.getenv("RECONNECT_INTERVAL", 1.0)), help="Reconnection interval in hours")
    
    args = parser.parse_args()
    
    if args.host == "user@host":
        print("Error: REMOTE_HOST not set in .env or via --host")
        sys.exit(1)
        
    try:
        run_tunnel(args.host, args.remote_port, args.local_port, args.interval)
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)
