# Local factory setup (rranjan14)

Choices for this fork. Upstream docs stay authoritative; this records what _we_ picked and what is
left to do.

| Decision          | Value                              | Why                                                                                                                                                      |
| ----------------- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Control plane     | Local container (`docker compose`) | No Cloudflare account. Same app code as Workers; SQLite on a volume replaces Durable Objects/D1, MinIO replaces R2.                                      |
| Sandbox provider  | `e2b`                              | The control plane calls the E2B REST API directly, so there is no shim service to deploy. Supports pause/resume, so idle sessions park instead of dying. |
| Web app           | `next dev` on :3000                | Not part of the compose stack by design.                                                                                                                 |
| Sign-in allowlist | `ALLOWED_USERS=rranjan14`          | `UNSAFE_ALLOW_ALL_USERS` stays off.                                                                                                                      |
| First target repo | A scratch repo under `rranjan14`   | Nothing touches `littlebirdai/littlebird` until the loop is proven.                                                                                      |

There is **no local-Docker sandbox provider**. `SANDBOX_PROVIDER` accepts only
`modal | daytona | vercel | opencomputer | e2b`. Agents always execute in a hosted sandbox.

## Done

- Forked to `rranjan14/background-agents`, `upstream` remote points at
  `ColeMurray/background-agents`.
- `npm install` (npm 11 gates install scripts; `esbuild`, `fsevents`, `sharp`, `workerd` are
  approved).
- Baseline verified: `npm run typecheck` clean, `npm test` 1709/1709 passing.
- `.env` created from `.env.example` with generated secrets: `TOKEN_ENCRYPTION_KEY`,
  `PROVIDER_ACCOUNTS_ENCRYPTION_KEY`, `REPO_SECRETS_ENCRYPTION_KEY`, `IMAGE_CALLBACK_TOKEN_PEPPER`,
  `BROWSER_AUTH_SECRET`, `SERVICE_AUTH_SECRET_WEB`, and one MinIO password shared by
  `MINIO_ROOT_PASSWORD`, `AWS_SECRET_ACCESS_KEY` and `LITESTREAM_SECRET_ACCESS_KEY`.
- `packages/web/.env.local` points at `http://localhost:8787` and carries the matching
  `SERVICE_AUTH_SECRET`.
- Stack up: 78 migrations applied, cron and alarm clock running, Litestream replicating `global.db`
  to the `backups` bucket.

Both env files are gitignored. Neither is in this repo.

## Run it

```bash
docker compose up -d --build          # control plane on :8787
curl -s localhost:8787/healthz        # migrations, sessions, cron, jobs
npm run dev -w @open-inspect/web      # UI on :3000
docker compose down                   # volume survives; `down -v` deletes it
```

## Left to do

### 1. GitHub App — done

`open-inspect-rranjan14` (app `4982553`, installation `162586559`, bot
`open-inspect-rranjan14[bot]`). Credentials verified: the app JWT authenticates and the installation
token lists repositories. Permissions are exactly Contents R/W, Pull requests R/W, Issues R/W,
Actions R, Checks R, Metadata R.

Created with `scripts/local-github-app.py`, which sends GitHub a pre-filled App Manifest and writes
every credential into `.env` itself. Re-run it with `--name` for a second app, or `--install-only`
to re-resolve an installation id.

No webhook is set. GitHub validates `hook_attributes.url` on submission even when `active` is false
and refuses any host it cannot reach, so the key is omitted until github-bot has a public URL. Pass
`--hook-url` then.

> **Installed on all repositories.** `repository_selection` is `all`, so the app can write to every
> repo under `rranjan14` — about thirty of them. Narrow it at
> <https://github.com/settings/installations/162586559> → _Only select repositories_. Autonomous
> agents hold this token.

### 2. E2B template — done

Built and verified (`py5sz6sf64euaukk3cys`, 2 cpu / 4096 MB, alias
`open-inspect-sandbox-717d5d2aa5da-1789677100517008000`). `E2B_API_KEY`, `E2B_TEMPLATE_ID`,
`E2B_SANDBOX_TIMEOUT_SECONDS=3300` and `E2B_AUTO_PAUSE=true` are set, and the control plane has
them.

`E2B_TEMPLATE_ID` means two different things and the build silently disagrees with the runtime about
which. `build-template.py` treats it as a **name prefix** and publishes under
`{prefix}-{buildHash}-{nanos}`; the control plane treats it as the **template ID** to create
sandboxes from. Setting it to the plain name you built with leaves the runtime pointing at a
template that was never published. Take the `reference` the build prints and put that in `.env`.

To rebuild after changing `packages/sandbox-runtime/src` or the image packages:

```bash
cd packages/e2b-infra
E2B_API_KEY=… E2B_TEMPLATE_ID=open-inspect-sandbox uv run python build-template.py
# then copy the printed reference into E2B_TEMPLATE_ID in .env and: docker compose up -d app
```

### 3. Tunnel — done

`WORKER_URL=https://fine-collected-technological-henry.trycloudflare.com`, verified reaching
`/healthz` through the tunnel. `WEB_APP_URL` stays `http://localhost:3000`, because browser sign-in
is origin-bound.

A `trycloudflare` quick tunnel gets a **new hostname every time cloudflared restarts**, and a stale
`WORKER_URL` fails sandboxes at the point where the bridge phones home, which reads as a session
that never becomes ready. After any cloudflared restart:

```bash
sed -i '' "s|^WORKER_URL=.*|WORKER_URL=<new-url>|" .env && docker compose up -d app
```

A named tunnel on a domain you own avoids the churn, and is worth it as soon as this is more than an
experiment.

### 4. Anthropic key

`ANTHROPIC_API_KEY` in `.env` only reaches sandboxes on the Modal and OpenComputer providers. On E2B
the key has to go in the **web app's secret store** through the UI. Set it in both places; the
secret store wins either way.

### 5. First session

Sign in at :3000, add the scratch repo, and give an agent a trivial task. Confirm the PR lands with
your name on it, not the bot's.

## After that

- Add `.openinspect/setup.sh` and `.openinspect/start.sh` to the target repo. `setup.sh` provisions
  (skipped when a prebuilt image or snapshot is used, non-fatal on a fresh session, fatal during an
  image build); `start.sh` runs on every non-build session and a failure there halts startup.
- Port the existing `.claude/skills/` from littlebird as managed skills.
- Slack, GitHub and Linear bots: `docs/integrations/`. Each needs its own `SERVICE_AUTH_SECRET_*`.
- Cron automations and webhook triggers: `docs/AUTOMATIONS.md`.

## Caveats

The tunnel puts the control plane on the public internet. `APP_BIND_ADDRESS` stays on loopback, but
cloudflared runs on the host and forwards to it, so anyone with the hostname reaches `/healthz` and
every unauthenticated route. Session routes need their tokens and sign-in needs `ALLOWED_USERS`, but
treat the URL as a secret and stop cloudflared when not testing.

Upstream is explicit that this is **single-tenant**: every user is trusted and reaches the same
repositories. Keep it on loopback, keep `ALLOWED_USERS` tight, and do not expose :8787 without Caddy
and a real hostname.
