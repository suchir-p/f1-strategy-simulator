# Running and deploying

## Locally

```bash
docker compose up --build        # then open http://localhost:3000
```

Four containers: PostgreSQL, the API (FastAPI + the compiled C++ engine,
which applies database migrations at startup), the dashboard (a Next.js
server that forwards `/api/*` to the API), and nginx in front of it as the
only public entry point. Local defaults need no configuration. The database
(5432) and API (8000) are bound to localhost for local tools and tests; the
dashboard isn't published; nginx listens on 3000.

The CI job `stack` (`.github/workflows/engine.yml`) builds and starts this
stack from a clean checkout on every push and runs a simulation through it,
then restarts it in production mode with a generated password and checks that
it serves, hides the job list and enforces the rate limit, including against
faked `X-Forwarded-For` headers.

## Before anyone else can reach it

```bash
cp .env.example .env     # then edit: set F1SIM_ENV=production and a real POSTGRES_PASSWORD
docker compose up --build -d
```

`F1SIM_ENV=production` changes the API's behavior:

| | development | production |
| --- | --- | --- |
| Database password | anything | refuses to start with a default or < 12 characters |
| `GET /jobs` (everyone's jobs) | listed | 404 |
| Submissions per client per minute | unlimited | 20 simulations/searches, 120 race replays |
| `X-Test-Action` failure injection | if `F1SIM_TEST_HOOKS` | refuses to start if enabled |

What stays the same, because it already applies everywhere:

* **Bounded work.** A fixed worker pool, a cap on pending jobs (HTTP 429 when
  full), a cap on races per job and candidates per search (HTTP 422), and a
  semaphore on inline race replays.
* **Job results are capability URLs.** A job is reachable only through its
  random UUID4 id (122 random bits), which only its submitter received. With
  the job list hidden there is no way to enumerate other people's jobs. There
  are no accounts, and results contain nothing personal: anyone holding a
  job's link can see its result, which is also what makes results shareable.
* **Resource limits** on every container (`deploy.resources.limits` in
  `docker-compose.yml`): the API to 3 CPUs and 1 GB, sized for one worker × 2
  OpenMP threads. Raise `F1SIM_MAX_WORKERS` and the limits together.

## Rate limiting and client addresses

Limits are per client, per minute, in memory (one API process; several
replicas would need a shared store). Requests reach the API through nginx and
the dashboard, so compose sets `F1SIM_TRUST_FORWARDED_FOR=true` and the API
keys on the first `X-Forwarded-For` address. That address has to be one the
client can't choose:

* **Next.js alone isn't enough.** It fills in `X-Forwarded-For` from the
  connection only when the header is missing, and passes along a header the
  client sent. Tested: with the dashboard exposed directly, a client with a
  rate limit of 1 got a second 202 just by adding a fake header.
* **So nginx decides** (`deploy/nginx/default.conf.template`): it overwrites
  `X-Forwarded-For` with the address that actually connected, and the
  dashboard isn't published, so there is no way around nginx. Tested through
  the full chain: fake headers, single or comma-separated, get 429 once the
  real client's budget is spent.
* **Behind a platform load balancer**, every connection comes from the
  balancer. Set `F1SIM_TRUSTED_PROXY_CIDR` to its address range: nginx then
  takes the visitor's address from the balancer's header (realip module) and
  forwards only that. Tested with a second loopback address standing in for
  the balancer: visitors behind it get separate budgets, while the same
  header from any other address is ignored. Use the range your platform
  documents; trusting too wide a range would let clients choose their address
  again.
* nginx also applies a coarse per-address limit to every request (20 per
  second, bursts of 60) against floods of page loads.

Even if all of this were bypassed, the pending-job cap still bounds the total
load; the per-client limit is about fairness between visitors.

## Other layouts aren't covered by these tests

Everything above, and what CI checks, assumes the whole compose stack runs on
one host (a VPS or a container host) with nginx in front. Splitting it changes
the request path, and the protections have to be re-checked for that path:

* **Dashboard on Vercel (or similar), API elsewhere.** The dashboard's server
  forwards `/api/*` to the API, so every request reaches the API from the
  platform's servers, and nginx isn't on that path at all. The API then sees
  either the platform's address for everyone (one shared budget) or, if it
  trusts `X-Forwarded-For`, whatever header the platform passes along, which
  is only safe if the platform overwrites it rather than appending to a
  client-supplied value. Check the platform's documented behavior, set
  `F1SIM_TRUST_FORWARDED_FOR` accordingly, and repeat the spoofing test
  (a burst of requests with a fake `X-Forwarded-For` should still get 429).
  The API must also not be reachable directly from the internet, or anyone
  can skip the dashboard and set the header themselves: restrict it to the
  platform's egress addresses or put it behind its own nginx.
* **Several API replicas.** The rate limiter is in memory per process, so
  each replica keeps its own count; a shared store (Redis) would be needed.

None of this is set up or tested here.

## Checklist

* `.env` exists, is not committed (it is in `.gitignore`), and sets
  `F1SIM_ENV=production` and a long random `POSTGRES_PASSWORD`.
* Only the proxy port (3000 by default) is reachable from outside. Terminate
  HTTPS in front of it (a platform load balancer, or certificates added to
  the nginx config).
* If a load balancer sits in front, `F1SIM_TRUSTED_PROXY_CIDR` is set to its
  documented address range, and nothing wider.
* `F1SIM_MAX_WORKERS × F1SIM_OMP_THREADS_PER_WORKER` ≤ the CPUs given to the
  API container.
* Back up the `f1sim-pgdata` volume if job history matters; the calibration
  data is reloaded from the bundle at every start.
