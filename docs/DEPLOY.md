# Deploying MeshToMod

One Linux server running two containers: **web** (Caddy: the built app, HTTPS, and a proxy for
`/api`) and **api** (FastAPI, the job queue, and one process per conversion). No database, no
Redis: jobs live in the API process and results on a volume for an hour.

```
browser ──HTTPS──> web (Caddy :443) ──/api──> api (:8000, not published) ──spawns──> job process
```

## Server size

A conversion of a 2.2M-triangle scan peaks around 3 GB and takes 5 to 10 minutes; small parts
take seconds. Run one conversion at a time and queue the rest.

| Server | Running jobs | `MESHTOMOD_API_MEMORY` |
| --- | --- | --- |
| 4 vCPU, 8 GB (minimum) | 1 | 7g |
| 8 vCPU, 16 GB | 2 | 14g |

Use dedicated vCPUs if the host offers them: conversions are CPU-bound for minutes.

## First deploy

1. Point DNS at the server: `A` (and `AAAA`) records for `meshtomod.com`, `www`, and each
   redirect domain. **If DNS is on Cloudflare, set these records to "DNS only" (grey cloud).**
   Caddy gets its own certificates, and Cloudflare's proxy cuts requests at 100 s and uploads at
   100 MB on the free plan.
2. Open ports 80 and 443 (TCP) and 443 (UDP). Nothing else needs to be public.
3. On the server:
   ```sh
   git clone https://github.com/mopar04/nurbsmith.git && cd nurbsmith
   cp .env.example .env        # then edit: domain, redirects, donate link, sizing
   docker compose -f docker-compose.prod.yml up -d --build
   ```
4. Check: `https://meshtomod.com` loads, `https://meshtomod.com/api/health` answers, and a
   test part converts. `docker compose -f docker-compose.prod.yml logs -f api` shows errors.

## DigitalOcean, step by step

The pick for launch (prices read 2026-10-27): a **Basic droplet, Regular, 4 vCPU / 8 GB, $48/mo**,
5 TB transfer included. Backups add 20% ($9.60/mo); the server holds nothing you can't rebuild
from git and `.env`, so they're optional.

### 1. In the DigitalOcean dashboard (about 10 minutes)

1. **Create → Droplets.** Region: San Francisco (SFO3). Image: **Ubuntu 24.04 LTS**. Size: Basic,
   Regular, **8 GB / 4 CPUs**. Authentication: **SSH key** (add your Mac's `~/.ssh/id_ed25519.pub`;
   no password). Tick **Monitoring** (free). Hostname: `meshtomod-1`.
2. **Networking → Firewalls → Create.** Inbound: SSH (TCP 22) from your IP only, HTTP (TCP 80)
   and HTTPS (TCP 443) from anywhere, and a custom UDP 443 rule from anywhere (HTTP/3). Apply it
   to `meshtomod-1`. Use this firewall rather than `ufw` on the server: Docker opens published
   ports straight through `ufw`.
3. **DNS** (wherever the domain lives; Cloudflare for nurbsmith.com): `A` records for the domain,
   `www` and each redirect domain, pointing at the droplet's IPv4. On Cloudflare, set each to
   **DNS only** (grey cloud).

### 2. On the server (about 15 minutes)

```sh
# From your Mac: first login as root
ssh root@DROPLET_IP

# Your own user with sudo and your SSH key; then no more root logins
adduser --disabled-password --gecos "" meshtomod
usermod -aG sudo meshtomod
passwd meshtomod                       # for sudo only; SSH stays key-only
rsync -a ~/.ssh /home/meshtomod/ && chown -R meshtomod:meshtomod /home/meshtomod/.ssh
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
exit
```

```sh
ssh meshtomod@DROPLET_IP

# 2 GB swap: headroom so a big scan slows down instead of the kernel killing things
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile
sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Docker Engine and Compose from Docker's own repository
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker meshtomod && newgrp docker

# Ubuntu installs security updates on its own (unattended-upgrades); check it's on:
systemctl is-enabled unattended-upgrades
```

### 3. Get the code and start it

While the repo is private, the server needs a read-only deploy key (skip this once it's public):

```sh
ssh-keygen -t ed25519 -f ~/.ssh/meshtomod_deploy -N "" -C meshtomod-1
cat ~/.ssh/meshtomod_deploy.pub
# GitHub → repo → Settings → Deploy keys → Add key, paste it, leave "write access" OFF
printf 'Host github.com\n  IdentityFile ~/.ssh/meshtomod_deploy\n' >> ~/.ssh/config
git clone git@github.com:mopar04/nurbsmith.git meshtomod   # https://... once public
```

```sh
cd meshtomod
cp .env.example .env && nano .env     # domain, redirect domains, donate link
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f web   # watch Caddy get the certificates
```

Then open the site, convert a test part, and add an uptime check on `https://DOMAIN/api/health`
(DigitalOcean → Monitoring → Uptime, or any free HTTPS monitor).

## Updating

```sh
git pull && docker compose -f docker-compose.prod.yml up -d --build
```

A restart drops queued and running jobs (users see an error and convert again); finished
results survive on the `results` volume until they expire.

## Settings

All in `.env` (see `.env.example`); the API reads `MESHTOMOD_*` variables
(`backend/nurbsmith/config.py`).

| Variable | Default | What it does |
| --- | --- | --- |
| `MESHTOMOD_MAX_UPLOAD_MB` | 200 | Per file. Caddy caps the whole request at 420 MB. |
| `MESHTOMOD_MAX_FACES` | 2,500,000 | Bigger scans are refused with "decimate it first". |
| `MESHTOMOD_MAX_RUNNING_JOBS` | 1 | Conversions at once. |
| `MESHTOMOD_MAX_QUEUED_JOBS` | 4 | Waiting jobs; more get "busy, try again" (503). |
| `MESHTOMOD_JOB_TIMEOUT_S` | 900 | A job running longer is killed (504). |
| `MESHTOMOD_JOB_MEMORY_MB` | 0 (off) | Per-job memory cap; fails one job instead of the server. |
| `MESHTOMOD_JOBS_PER_CLIENT_HOUR` | 20 | Per IP address (429 after). One job at a time each. |
| `MESHTOMOD_RESULT_TTL_MIN` | 60 | Results are deleted after this. The privacy page says one hour. |
| `MESHTOMOD_CLIENT_IP_HEADER` | X-Real-IP in prod | Where the client address comes from behind Caddy. |

## Privacy and the AGPL

- Uploaded scans are deleted when their job ends; results after `MESHTOMOD_RESULT_TTL_MIN`.
  The API keeps no access log (`--no-access-log`). `frontend/public/privacy.html` says this; keep
  it true when changing any of it.
- The AGPL requires offering the source to people who use the hosted app. The footer links to
  `VITE_SOURCE_URL`, so **that repository must be public before the site is.**

## Monitoring

`/api/health` is the check to point an uptime monitor at (any free one that does HTTPS checks).
Docker restarts a crashed container (`restart: unless-stopped`), and the API image has a
healthcheck. There is no error tracking service; read `docker compose logs api`.
