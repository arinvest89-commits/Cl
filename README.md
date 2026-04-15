# Cl — Paperclip AI Instance

Paperclip AI agent orchestration platform running at `http://167.71.137.62:3100`.

## Quick start

```bash
bash start.sh
```

## Manual setup

1. Install: `npm install -g paperclipai`
2. Start PostgreSQL and create the `paperclip` database
3. Run: `paperclipai run -c paperclip.config.json`
4. Open: `http://167.71.137.62:3100/onboarding`

## Config

- `paperclip.config.json` — server/database/auth configuration template
- `start.sh` — full bootstrap script (installs deps, starts DB, runs server)

## Environment

Required environment variable (set in `~/.paperclip/instances/default/.env`):

```
PAPERCLIP_AGENT_JWT_SECRET=<generated on first run>
```
