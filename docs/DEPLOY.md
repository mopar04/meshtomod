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
