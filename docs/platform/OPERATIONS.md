# Operating PIA

## Run it on this computer

```bash
pia serve --open
```

or double-click `run-pia.bat`. The web app is at http://127.0.0.1:8765 and the agent runs a cycle every ten
minutes while the window is open. Nothing is reachable from other machines and no login is needed.

- `pia serve --no-agent` serves the app without fetching anything.
- `pia run-once` runs a single cycle and exits; `--no-models` uses no API key at all.
- The System page shows the last runs, source health and model cost, and has a "Run a cycle now" button.

## What a cycle does and costs

Every ten minutes: fetch the sources that are due (each has its own interval, 1 to 12 hours), classify and score
new items, send up to 160 of the most promising to the assessment model (Groq), extract opportunities, re-rank,
link related items, decide notifications, and write up to 12 briefs for the top of Home (Groq). Once a day: look
for new sources, take a backup. Groq is the only external model provider; Jev is switched off (see
ARCHITECTURE.md, decision A15).

Measured on the first real run (2026-11-15): 1,325 new items from 76 sources in 133 seconds; assessing 120
items cost $0.008; 11 briefs cost $0.006. A steady day is expected to stay in the low tens of cents. `config/models.toml` sets a daily budget (default $1.00) at which model calls stop for the
day; everything else keeps running.

Groq's free tier rate-limits requests. The retry layer waits and continues, so a cycle with many new items can
take several minutes. That is normal.

## Backups and recovery

- A consistent snapshot is written to `data/backups/` once a day (the 14 newest are kept). `pia backup` takes
  one on demand. Snapshots use SQLite's online backup API, so they are safe while PIA is running.
- Before any schema upgrade PIA copies the database file to `data/backups/` first.
- To restore: stop PIA, copy a snapshot over `data/platform.db`, start PIA.
- Everything personal lives in `data/` and three git-ignored files (`.env`, `config/personal.toml`,
  `config/interests.toml`). Back those up together.

## Windows notifications

See [companion/README.md](../../companion/README.md). In short: create a device token in PIA (Notifications,
Devices), then run `companion\install.ps1 -Url <address> -Token <token>` once.

## Running it in the cloud

The goal is that acquisition continues when the laptop is off. PIA is one process with one file of state, so
any host that runs a container with a persistent disk works. **Deployment is not done by the build: it costs
money and needs your accounts.**

What you need on any host:

1. Build the image from the `Dockerfile` in the repository root.
2. Mount a persistent volume at `/app/data`.
3. Set environment variables: `PIA_PASSWORD` (required: PIA refuses to listen publicly without one),
   `GROQ_API_KEY`, and `PIA_ALLOWED_HOSTS` set to your domain. No other model key is needed or used.
4. Put HTTPS in front (every option below does this for you) and expose port 8765.
5. Copy your `config/personal.toml` into the volume or image.

| Option | Rough monthly cost | Notes |
|---|---|---|
| A small VPS (Hetzner, DigitalOcean, Lightsail) with Docker and Caddy | $4 to $6 | Most control; you patch the machine |
| Fly.io: one shared-CPU machine and a 1 GB volume | about $3 to $5 | Good fit: persistent volume, automatic HTTPS, one command to deploy |
| Railway or Render with a disk | $5 to $10 | Simplest dashboards; check that the disk is persistent on your plan |
| A home server or Raspberry Pi behind Tailscale | hardware only | Private by default; the companion and your phone reach it over the tailnet |

Prices are indicative and change; check the provider. Recommendation: Fly.io or a small VPS. A serverless or
"scale to zero" platform is the wrong shape, because the agent is a long-running loop and the state is a file.

After deploying, point the companion at the HTTPS address and sign in to the web app with the password.

### Security checklist for a public deployment

- A long `PIA_PASSWORD`; HTTPS only; `PIA_ALLOWED_HOSTS` set.
- Keys only in the host's secret store, never in the image.
- The volume backed up by the host, or `data/backups/` copied off the machine on a schedule.
- Device tokens revoked for any computer you stop using.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Items appear but have no summaries | No `GROQ_API_KEY`, or the daily model budget was reached (System page) |
| "Sign in required" in the app | A password is set; sign in, or `pia set-password --clear` for local-only use |
| A source shows "Failing repeatedly" | Open Sources to see the error. Feeds move; remove it and add the new address |
| The agent seems stuck on "working" | A long first cycle under rate limits. The System page shows the run in progress |
| `database schema is newer than this code` | The code is older than the database. Update the code; do not delete the database |
| Nothing new for hours | Check System, Sources. If every source fails, the machine is offline |
