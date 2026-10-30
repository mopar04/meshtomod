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
   git clone https://github.com/mopar04/meshtomod.git && cd meshtomod
   cp .env.example .env        # then edit: domain, redirects, donate link, sizing
   docker compose -f docker-compose.prod.yml up -d --build
   ```
4. Check: `https://meshtomod.com` loads, `https://meshtomod.com/api/health` answers, and a
   test part converts. `docker compose -f docker-compose.prod.yml logs -f api` shows errors.

## DigitalOcean, step by step

The pick (prices read on DigitalOcean's pricing page 2026-10-29): a **Basic droplet, Regular
CPU, 4 vCPU / 8 GB, $48/mo**, 160 GB disk and 5 TB transfer included. Backups add 20%
($9.60/mo); the server holds nothing that can't be rebuilt from git and `.env`, so they're
optional. App Platform was ruled out: an 8 GB container there is $98 (2 vCPU) to $156 (4 vCPU)
a month, with a 4 GB disk cap and a sandboxed runtime (see DECISIONS, 2026-10-29).

Nobody needs to log in to the server. The droplet's initialization script runs
`deploy/bootstrap.sh`, which sets the server up and starts the site; after that,
`deploy/update.sh` deploys new commits on `main` by itself.

### 1. Create the droplet (about 10 minutes, in the DigitalOcean dashboard)

1. **Create → Droplets.** Region: San Francisco (SFO3). Image: **Ubuntu 24.04 (LTS) x64**.
   Size: Basic, Regular, **$48/mo (8 GB / 4 CPUs)**.
2. Authentication: **SSH key** if you have one, otherwise **Password** (a long one, saved in a
   password manager; day to day you won't need it). The bootstrap turns off password logins
   over SSH; the dashboard's web console still works.
3. Tick **Add improved metrics monitoring and alerting** (free).
4. **Advanced options → Add initialization scripts**, paste:
   ```sh
   #!/bin/bash
   curl -fsSL https://raw.githubusercontent.com/mopar04/meshtomod/main/deploy/bootstrap.sh | bash
   ```
5. Hostname `meshtomod-1`, then **Create Droplet** and copy its IPv4 address.

Optional, for defence in depth: **Networking → Firewalls**, inbound TCP 22, 80, 443 and UDP 443,
applied to the droplet. Only those ports listen anyway (Docker publishes 80 and 443).

### 2. DNS (Cloudflare, about 5 minutes)

For `meshtomod.com` add `A` records `@` and `www` with the droplet's IPv4; for `meshtomodel.com`
the same two. Set each to **DNS only** (grey cloud): Caddy gets its own certificates, and
Cloudflare's proxy cuts requests at 100 s and uploads at 100 MB.

### 3. Wait and check

The first build takes about 10 minutes. Then `https://meshtomod.com` loads,
`https://meshtomod.com/api/health` answers, and the other three names redirect to it.
If it doesn't come up, the dashboard's **Console** shows the setup log:
`tail -n 50 /var/log/cloud-init-output.log`.

### Doing it by hand instead

On any Ubuntu 24.04 server, as root: `bash deploy/bootstrap.sh` from a clone, or the `curl`
line above. Without the updater: `git pull && docker compose -f docker-compose.prod.yml up -d --build`.

## Updating

Automatic. Every 5 minutes `meshtomod-update.timer` runs `deploy/update.sh`, which:

1. Fetches `main`. Nothing new: done.
2. Asks GitHub for the commit's checks (public API, no token). It deploys only when every check
   has finished as success, skipped or neutral; a failed commit is never deployed.
3. Waits while a conversion is running or queued (`/api/health` reports `jobs`), at most an
   hour, because a restart drops them.
4. Rebuilds and restarts, then waits up to 5 minutes for the API healthcheck and Caddy. If
   either fails, it puts the previous commit back and skips that commit until a newer one lands.

So merging a PR is deploying it. Log: `journalctl -u meshtomod-update`. To pause updates:
`systemctl stop meshtomod-update.timer`.

A restart drops queued and running jobs (users see an error and convert again); finished
results survive on the `results` volume until they expire. Edits to `.env` on the server stay
put (it isn't in git); apply them with `docker compose -f docker-compose.prod.yml up -d`.

## Settings

All in `.env` (see `.env.example`); the API reads `MESHTOMOD_*` variables
(`backend/meshtomod/config.py`).

| Variable | Default | What it does |
| --- | --- | --- |
| `MESHTOMOD_MAX_UPLOAD_MB` | 200 | Per file. Caddy caps the whole request at 420 MB. |
| `MESHTOMOD_MAX_FACES` | 5,000,000 | Bigger scans are refused with "decimate it first". Smaller ones over a level's limit (500k Rough, 1M Functional, 2M Precise) are simplified automatically. |
| `MESHTOMOD_MAX_RUNNING_JOBS` | 1 | Conversions at once. |
| `MESHTOMOD_MAX_QUEUED_JOBS` | 4 | Waiting jobs; more get "busy, try again" (503). |
| `MESHTOMOD_JOB_TIMEOUT_S` | 900 | A job running longer is killed (504). |
| `MESHTOMOD_JOB_MEMORY_MB` | 0 (off) | Per-job memory cap; fails one job instead of the server. |
| `MESHTOMOD_JOBS_PER_CLIENT_HOUR` | 20 | Per IP address (429 after). One job at a time each. |
| `MESHTOMOD_JOB_ABANDON_S` | 180 | A queued or running job no page has checked on for this long is stopped (the page closed). 0: never. |
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
