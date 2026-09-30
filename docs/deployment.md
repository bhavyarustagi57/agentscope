# Production-style Compose deployment

This is AgentScope's supported deployment package for a single Docker host. It deliberately contains
only the existing web, API, worker, PostgreSQL, and Redis architecture. It does not provision cloud
infrastructure, TLS, authentication, or a public ingress.

## Architecture

- **Web** serves the production Next.js build. Its `NEXT_PUBLIC_API_URL` is public browser
  configuration compiled into the image.
- **API** serves synchronous control-plane requests and exposes `/health`, `/ready`, and `/metrics`.
- **Worker** runs the existing Dramatiq jobs and durable lease/fencing recovery loops.
- **PostgreSQL** is the durable system of record and owns the `postgres_data` named volume.
- **Redis** coordinates ephemeral queue/runtime state. It is not evidence storage and has no volume.

Only web and API ports are published, bound to `127.0.0.1` by default. PostgreSQL and Redis remain on
the Compose network. Put a separately managed TLS reverse proxy in front only when the deployment
environment requires external access.

## Configure

Copy the production template to an ignored local file:

```powershell
Copy-Item .env.production.example .env.production
```

On macOS or Linux:

```sh
cp .env.production.example .env.production
```

Replace every placeholder in `.env.production`. `POSTGRES_PASSWORD` and the password inside
`DATABASE_URL` must match. Use a long random alphanumeric value, or URL-encode reserved characters in
`DATABASE_URL`. Set `CORS_ORIGINS` to the browser-visible web origin and `NEXT_PUBLIC_API_URL` to the
browser-visible API origin. Multiple CORS origins are comma-separated. `OPENAI_API_KEY` is optional
and remains backend-only; it is never a web build argument.

The commands below use this prefix:

```text
docker compose --env-file .env.production -f compose.production.yaml
```

## First deployment

Run these commands from the repository root, in order:

```text
docker compose --env-file .env.production -f compose.production.yaml config --quiet
docker compose --env-file .env.production -f compose.production.yaml build api web
docker compose --env-file .env.production -f compose.production.yaml up -d postgres redis
docker compose --env-file .env.production -f compose.production.yaml --profile tools run --rm migrate
docker compose --env-file .env.production -f compose.production.yaml --profile tools run --rm preflight
docker compose --env-file .env.production -f compose.production.yaml up -d api worker web
docker compose --env-file .env.production -f compose.production.yaml --profile tools run --rm smoke
```

Migration is intentionally explicit. Normal API and worker startup never runs Alembic. A failed
migration command exits nonzero and must be resolved before application services start. Preflight is
read-only and confirms configuration, PostgreSQL, Redis, and that the database revision matches the
repository head.

The smoke command performs bounded, read-only checks of API liveness, dependency readiness, one
paginated trace read, the frontend response, and the low-risk security headers. It never writes data,
enqueues work, or calls OpenAI.

## Operator commands

| Task | Command after the common Compose prefix |
| --- | --- |
| Validate configuration | `config --quiet` |
| Build API and web images | `build api web` |
| Start infrastructure | `up -d postgres redis` |
| Apply migrations | `--profile tools run --rm migrate` |
| Run preflight | `--profile tools run --rm preflight` |
| Start applications | `up -d api worker web` |
| Inspect status | `ps` |
| Inspect API/worker logs | `logs api worker` |
| Follow API/worker logs | `logs -f api worker` |
| Run deployment smoke | `--profile tools run --rm smoke` |
| Seed deterministic demo evidence | `--profile tools run --rm demo-seed` |
| Restart applications | `restart api worker web` |
| Stop safely and retain data | `down --timeout 30` |

Useful host checks with the template's default ports:

```text
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/ready
curl --fail "http://localhost:8000/api/v1/traces?page_size=1"
curl --fail http://localhost:3000/
```

`/health` means the API process is alive. `/ready` means PostgreSQL and Redis are currently usable.
A running container or successful liveness response is not the same as application readiness.
Frontend availability does not require OpenAI.

## Optional deterministic demo

After readiness succeeds, seed the existing synthetic workspace:

```text
docker compose --env-file .env.production -f compose.production.yaml --profile tools run --rm demo-seed
```

The command is optional, idempotent, and clearly labels its evidence **AgentScope Demo**. It does not
reset data, access an arbitrary repository, or call a paid provider.

Run the separate marker-owned Git boundary demonstration inside the API container so repository
validation and probing share its filesystem:

```text
docker compose --env-file .env.production -f compose.production.yaml exec -T api python scripts/run_bisection_demo.py
```

`BISECTION_MAX_COMMIT_RANGE`, `BISECTION_WORK_ROOT`, and
`BISECTION_EXECUTION_MAX_ATTEMPTS` are bounded in the environment template and passed consistently to
the API and worker image. The demo overrides only its own temporary work root and never targets the
AgentScope checkout.

## Persistence and safe restart

PostgreSQL data persists in the Compose-managed `agentscope-production_postgres_data` volume.
`docker compose down` removes containers and the network but retains that volume. Never use
`docker compose down -v` unless permanent evidence deletion is explicitly intended.

To prove persistence safely after seeding, record the deterministic trace ID
`agentscope-demo-workspace-v1-00`, stop only application containers, restart them, and read it again:

```text
docker compose --env-file .env.production -f compose.production.yaml stop web api worker
docker compose --env-file .env.production -f compose.production.yaml start api worker web
curl --fail http://localhost:8000/ready
curl --fail http://localhost:8000/api/v1/traces/agentscope-demo-workspace-v1-00
```

Worker shutdown has a 30-second grace period. Interrupted durable work remains recoverable through
the existing PostgreSQL leases and fencing. `WORKER_PROCESSES` and `WORKER_THREADS` bound the
Dramatiq runtime independently of the host CPU count; raise them only after measuring workload and
database capacity. API and web receive 10 seconds. Redis queue/runtime state may be recreated;
canonical evidence remains in PostgreSQL.

## Logs and diagnosis

API startup logs identify the environment mode. Readiness failures report only PostgreSQL/Redis
availability, migration mismatch is reported by preflight, worker logs show recovery scans, and API
logs show sanitized request/runtime failure types. These paths do not log credentials or connection
URLs.

- **Readiness/PostgreSQL failure:** run `ps`, inspect `logs postgres api`, then rerun preflight. Do not
  migrate until PostgreSQL is healthy.
- **Redis failure:** inspect `logs redis api worker`; PostgreSQL evidence remains canonical while queue
  coordination is unavailable.
- **Migration mismatch:** run the explicit `migrate` command, then rerun preflight. Never downgrade
  automatically.
- **CORS mismatch:** set `CORS_ORIGINS` to the exact browser origin, including scheme and port, then
  recreate API.
- **Frontend/API URL mismatch:** change `NEXT_PUBLIC_API_URL`, rebuild the web image, and recreate web;
  the value is compiled into browser assets.
- **Stale image:** rebuild the affected image and use `up -d --force-recreate` for that service.

For rollback, retain the PostgreSQL volume and restore the prior application image tag. Database
downgrades are never automatic; assess migration compatibility before starting an older image.
