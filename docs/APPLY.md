# Apply migrations to production (CT Postgres)

**Do not run this until Lamassu card A is done and Wil confirms.**

## Preconditions

- [ ] Reachable `HOST:5432` from seneschal
- [ ] Database `mnemosyne` exists and is empty (or only prior Mnemosyne migrations)
- [ ] Roles exist: `mnemosyne_migrator` (apply), plus ingest/app/ro as designed
- [ ] `DATABASE_URL` set in a secret store — **not** Reliquary, git, or chat
- [ ] CI green on the commit you are applying
- [ ] Wil verbal/written OK for first migrate

## Apply

```bash
cd /root/seneschal/repos/mnemosyne
source .venv/bin/activate   # or fresh venv + pip install -e .
export DATABASE_URL=postgresql://mnemosyne_migrator@HOST:5432/mnemosyne
mnemosyne migrate-status
mnemosyne migrate
mnemosyne migrate-status
```

Optional smoke (fixture only):

```bash
mnemosyne ingest-golden --no-migrate
mnemosyne reviews --limit 3
# delete fixture rows afterward if you do not want them in prod
```

## Rollback posture

v0.1 has no automatic down migrations. Restore from Postgres backup / snapshot
if a migrate fails mid-way. Prefer expand-only follow-on migrations.

## After apply

- Record **non-secret** facts: host:port, db name, migration filenames, commit SHA
- Under Stronghold ops notes (Lamassu) and shipyard/memory maps (Hephaestus)
- Never paste passwords or full credentialed URLs
