# Deployment: on-prem and cloud

The same application runs in both environments. The **only** thing that changes
is the storage backend, selected by the `TANRIC_STORAGE` setting in `.env`. No
application code branches on deployment type.

> All commands assume you are in the project root (the folder containing
> `requirements.txt`). On Windows PowerShell, set `$env:PYTHONPATH = "."` once
> per terminal session so the `tanric` package is importable when running
> scripts. On macOS/Linux use `export PYTHONPATH=.` instead.

---

## One-time setup (do once, before either mode)

```powershell
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
$env:PYTHONPATH = "."           # macOS/Linux: export PYTHONPATH=.
python scripts/generate_synthetic_data.py --out data/public
python -m pytest -q             # expect: 11 passed
```

---

## On-prem (local storage)

Data stays on the institution's own hardware and never leaves their network.

**`.env` storage section:**

```dotenv
TANRIC_STORAGE=local
TANRIC_DATA_ROOT=data
# (object lines commented out)
```

**Run:**

```powershell
$env:PYTHONPATH = "."
uvicorn tanric.api.app:app --port 8000
# open http://localhost:8000
```

The storage label at the top of the page reads `LocalStorageBackend`. The local
folder (or a mounted volume in production) is the only place data lives. This is
the right choice for groups with sensitive/regulated data or no cloud account.

### On-prem via Docker (production-style)

```bash
docker build -t tanric2 .
docker run -p 8000:8000 \
  -e TANRIC_STORAGE=local \
  -e TANRIC_DATA_ROOT=/data \
  -v /path/to/institution/storage:/data \
  tanric2
```

---

## Cloud (S3-compatible object storage)

The same app, pointed at object storage instead of a disk. A local demo using
**MinIO** (an S3-compatible store) proves the path end-to-end without an AWS
account.

**`.env` storage section** (comment out local, enable object):

```dotenv
# TANRIC_STORAGE=local
# TANRIC_DATA_ROOT=data
TANRIC_STORAGE=object
TANRIC_S3_BUCKET=tanric-data
TANRIC_S3_ENDPOINT=http://localhost:9000
TANRIC_S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=tanric
AWS_SECRET_ACCESS_KEY=tanric-secret
```

> The credentials `tanric` / `tanric-secret` must match the MinIO root user in
> `docker-compose.cloud.yml`. Use `localhost:9000` here because the seed script
> and `uvicorn` run on your host. (The app *inside* Docker uses `minio:9000` —
> already set in the compose file; don't change it.)

**Terminal 1 — start MinIO + the app container:**

```bash
docker compose -f docker-compose.cloud.yml up --build
```

Wait until the logs settle (MinIO up, bucket created, app booted). Leave it running.

**Terminal 2 — seed the public datasets into the bucket, then run the app:**

```powershell
$env:PYTHONPATH = "."
python scripts/seed_cloud.py
uvicorn tanric.api.app:app --port 8000
```

**Open two tabs:**

- App: <http://localhost:8000> — storage label should read `ObjectStorageBackend`
- MinIO console: <http://localhost:9001> — login `tanric` / `tanric-secret`

Upload a private dataset in the app, then in the MinIO console open bucket
`tanric-data` -> `private` -> `<user>` to watch the upload appear as objects —
proof that user data is physically stored in the object store, per tenant, under
`private/<user>/<dataset>/`.

**Stop:** `Ctrl+C` in Terminal 2, then `Ctrl+C` in Terminal 1, then
`docker compose -f docker-compose.cloud.yml down`.

### Moving to real AWS S3

No code change — only configuration. In `.env`, set real credentials and **drop**
`TANRIC_S3_ENDPOINT` so boto3 talks to real AWS instead of MinIO:

```dotenv
TANRIC_STORAGE=object
TANRIC_S3_BUCKET=your-bucket
TANRIC_S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-real-key
AWS_SECRET_ACCESS_KEY=your-real-secret
# TANRIC_S3_ENDPOINT intentionally omitted
```

---

## The point

The only difference between on-prem and cloud is the `.env` storage block —
`local` vs `object`. Same app, same UI, same analysis, same upload flow. That is
the architectural claim, demonstrated by configuration alone.

---

## Proof in the test suite

`tests/test_architecture.py::test_object_backend_against_real_s3_api` runs the
cloud path against a real S3 protocol server (moto) on every `pytest` run: it
writes a private dataset to object storage, reads it back, runs analysis, and
checks tenant isolation. So the cloud path is verified, not just asserted.

---

## What is still a next step (honest scope)

- Server-side encryption, bucket policies, and per-tenant IAM/prefix scoping are
  configured at deploy time in the object backend — hooks are marked in
  `tanric/storage/object_store.py` but not enabled in the demo.
- Sessions are in-memory; multi-instance cloud deployments need a shared session
  store (e.g. Redis) and HTTPS + secure cookies.
