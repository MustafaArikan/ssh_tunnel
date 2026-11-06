# SSH Tunnel Project

Auto-reconnecting **reverse** SSH tunnels. The manager dials out from this
machine to one or more SSH servers and asks each of them to listen on some
remote ports that forward back to ports on this machine. That is how this
desktop (behind NAT, no public IP) stays reachable from anywhere.

## Layout

```
tunnel_manager.py   supervisor: one thread per connection, restart/recycle logic
.env                connection definitions (gitignored)
ssh_tunnel.service  systemd unit
```

## Configuration

Connections live in `.env`, one `[section]` per connection:

| Key | Meaning | Default |
| --- | --- | --- |
| `HOST` | `user@ip` of the SSH server to dial out to | required |
| `SSH_PORT` | SSH port on that server | `22` |
| `IDENTITY_FILE` | private key to authenticate with | ssh default |
| `FORWARDS` | comma-separated `REMOTE:LOCAL` reverse forwards | required |
| `RECONNECT_INTERVAL` | hours before a healthy tunnel is recycled | `1.0` |
| `ALLOW_PASSWORD` | `yes` to permit an interactive password prompt | `no` |

Check what it resolved, without connecting:

```bash
python3 tunnel_manager.py --list
python3 tunnel_manager.py --only thinkstation   # run a subset
```

## Running

```bash
python3 tunnel_manager.py           # supervise all connections, Ctrl-C to stop
```

As a service (needs sudo):

```bash
sudo cp ssh_tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ssh_tunnel
systemctl status ssh_tunnel
journalctl -u ssh_tunnel -f
```

Restart after editing `.env`:

```bash
sudo systemctl restart ssh_tunnel
```

## Authentication

Unattended runs use `BatchMode=yes`, so a connection **must** have working
key auth or it will fail immediately and retry forever:

