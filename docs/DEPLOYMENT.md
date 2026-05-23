# Deployment: on-prem and cloud

The same application image runs in both environments. The only thing that
changes is the storage backend, selected by the `TANRIC_STORAGE` environment
variable. No application code branches on deployment type.

## On-prem (local storage)

Data stays on the institution's own hardware and never leaves their network.

```bash
docker build -t tanric2 .
docker run -p 8000:8000 \
  -e TANRIC_STORAGE=local \
  -e TANRIC_DATA_ROOT=/data \
  -v /path/to/institution/storage:/data \
  tanric2
# open http://localhost:8000
```

This is the right choice for groups with sensitive or regulated data, or no
cloud account. The mounted volume is the only place data lives.

## Cloud (S3-compatible object storage)

The same image, pointed at object storage instead of a disk. A local demo using
**MinIO** (an S3-compatible store) proves the path end-to-end without an AWS
account:

```bash
docker compose -f docker-compose.cloud.yml up --build
# in another shell, seed the public datasets into the bucket:
TANRIC_S3_ENDPOINT=http://localhost:9000 TANRIC_S3_BUCKET=tanric-data \
AWS_ACCESS_KEY_ID=tanric AWS_SECRET_ACCESS_KEY=tanric-secret \
  python scripts/seed_cloud.py
# open http://localhost:8000   (app)   and   http://localhost:9001 (MinIO console)
```

You can watch private uploads appear as objects in the MinIO console — proof
that user data is physically stored in the object store, per tenant, under
`private/<user>/<dataset>/`.

### Moving to real AWS S3

No code change — only configuration:

```bash
docker run -p 8000:8000 \
  -e TANRIC_STORAGE=object \
  -e TANRIC_S3_BUCKET=your-bucket \
  -e TANRIC_S3_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... \
  tanric2
```

(Drop `TANRIC_S3_ENDPOINT` so boto3 talks to real AWS instead of MinIO.)

## Proof in the test suite

`tests/test_architecture.py::test_object_backend_against_real_s3_api` runs the
cloud path against a real S3 protocol server (moto) on every `pytest` run:
it writes a private dataset to object storage, reads it back, runs analysis, and
checks tenant isolation. So the cloud path is verified, not just asserted.

## What is still a next step (honest scope)

- Server-side encryption, bucket policies, and per-tenant IAM/prefix scoping are
  configured at deploy time in the object backend — hooks are marked in
  `tanric/storage/object_store.py` but not enabled in the demo.
- Sessions are in-memory; multi-instance cloud deployments need a shared session
  store (e.g. Redis) and HTTPS + secure cookies.
