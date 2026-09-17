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

### 1. GitHub App

One App covers both repository access and sign-in. At <https://github.com/settings/apps>:

- Name: globally unique, e.g. `open-inspect-rranjan14`
- Homepage URL: `http://localhost:3000`
- Callback URL: `http://localhost:3000/api/auth/callback/github`
- Webhook: leave **inactive** for now (the GitHub bot turns it on later)
- Keep **User-to-server token expiration** active. Without it GitHub issues no refresh token, so PRs
  get attributed to the bot instead of to you.
- Repository permissions: Contents **R/W**, Pull requests **R/W**, Issues **R/W**, Actions **R**,
  Checks **R**, Metadata **R**
- Install it on **one scratch repo only**.

Then fill in `.env`: `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID` (the number at the end of the
install URL), `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, and `GITHUB_APP_PRIVATE_KEY`. The private
key is PEM text on one line — either double-quoted with real newlines, or with a literal `\n` in
place of each newline.

### 2. E2B template

```bash
cd packages/e2b-infra
uv sync --frozen
export E2B_API_KEY=e2b_…
export E2B_TEMPLATE_ID=open-inspect-sandbox
uv run python build-template.py
```

Set `E2B_API_KEY` and `E2B_TEMPLATE_ID` in `.env` too. On the Hobby tier the runtime cap is about an
hour, so set `E2B_SANDBOX_TIMEOUT_SECONDS=3300`.

### 3. Tunnel

A sandbox opens a WebSocket back to `WORKER_URL`, which is `http://localhost:8787` right now and
unreachable from E2B. Start a tunnel and point `WORKER_URL` at it:

```bash
cloudflared tunnel --url http://localhost:8787
```

`WEB_APP_URL` stays `http://localhost:3000`, because browser sign-in is origin-bound.

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

Upstream is explicit that this is **single-tenant**: every user is trusted and reaches the same
repositories. Keep it on loopback, keep `ALLOWED_USERS` tight, and do not expose :8787 without Caddy
and a real hostname.
