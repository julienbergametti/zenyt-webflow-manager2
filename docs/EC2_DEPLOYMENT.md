# EC2 Deployment & SSH Access

## EC2 Instance

| Field       | Value                          |
|-------------|--------------------------------|
| Host alias  | `zenyt`                        |
| IP          | `54.164.157.91`                |
| User        | `ec2-user`                     |
| SSH key     | `~/.ssh/zenyt-experiments`     |
| Remote dir  | `/opt/zenyt/repo/zenyt-webflow-manager2` |
| Service     | `zenyt-app` (systemd)          |
| URL         | http://www.zenyt-experiments.com |

## SSH Config

Stored at `~/.ssh/config`:

```
Host zenyt
  HostName 54.164.157.91
  User ec2-user
  IdentityFile ~/.ssh/zenyt-experiments
  StrictHostKeyChecking no
```

Once configured, connect with:

```bash
ssh zenyt
```

## Deployment

### Deploy code (excludes data & secrets)

```bash
./deploy.sh
```

This uses `rsync` to sync code to EC2 and restarts the service. It **excludes**:
- `*.json` (so `leads_data.json` is never overwritten on EC2)
- `*.pem`, `.env`, `__pycache__/`, `venv/`, `.git/`

### Manual file sync

To push `leads_data.json` from local to EC2:

```bash
scp dashboard/leads_data.json zenyt:/opt/zenyt/repo/zenyt-webflow-manager2/dashboard/leads_data.json
```

To pull `leads_data.json` from EC2 to local:

```bash
scp zenyt:/opt/zenyt/repo/zenyt-webflow-manager2/dashboard/leads_data.json dashboard/leads_data.json
```

## Service Management

```bash
# Restart the app
ssh zenyt "sudo systemctl restart zenyt-app"

# Check status
ssh zenyt "sudo systemctl status zenyt-app --no-pager"

# View logs (live)
ssh zenyt "sudo journalctl -u zenyt-app -f"

# View last 100 log lines
ssh zenyt "sudo journalctl -u zenyt-app -n 100 --no-pager"
```

## Important Notes

- **`leads_data.json` lives independently on EC2 and local.** The deploy script excludes it. If you need to sync data, do it manually with `scp` (see above).
- **`.env` also lives independently on EC2.** API keys must be configured directly on the server.
- The app writes to `dashboard/leads_data.json` on every lead action (sync, push, reject, enrich). No database — just this JSON file.
