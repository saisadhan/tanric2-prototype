"""
Seed the synthetic public datasets into an S3-compatible object store.

Used by the cloud deployment demo (docker-compose.cloud.yml). Reads the local
synthetic datasets under data/public/ and uploads each one's three objects
(manifest.json, expression.parquet, clinical.parquet) into the bucket under the
public/ prefix — the exact layout ObjectStorageBackend expects.

Usage (after `docker compose -f docker-compose.cloud.yml up`):

    TANRIC_S3_ENDPOINT=http://localhost:9000 \
    TANRIC_S3_BUCKET=tanric-data \
    AWS_ACCESS_KEY_ID=tanric AWS_SECRET_ACCESS_KEY=tanric-secret \
    python scripts/seed_cloud.py

Run it once; the public datasets then show up in the web UI.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import boto3
import pandas as pd


def main() -> None:
    bucket = os.environ.get("TANRIC_S3_BUCKET", "tanric-data")
    endpoint = os.environ.get("TANRIC_S3_ENDPOINT", "http://localhost:9000")
    region = os.environ.get("TANRIC_S3_REGION", "us-east-1")

    s3 = boto3.client("s3", endpoint_url=endpoint, region_name=region)

    # Make sure the bucket exists (harmless if it already does).
    try:
        s3.create_bucket(Bucket=bucket)
    except Exception:
        pass

    public_root = Path("data/public")
    if not public_root.exists():
        raise SystemExit("data/public not found — run "
                         "scripts/generate_synthetic_data.py first.")

    count = 0
    for ds_dir in sorted(public_root.iterdir()):
        if not (ds_dir / "manifest.json").exists():
            continue
        ds = ds_dir.name
        prefix = f"public/{ds}/"

        s3.put_object(Bucket=bucket, Key=prefix + "manifest.json",
                      Body=(ds_dir / "manifest.json").read_bytes())
        for fname in ("expression.parquet", "clinical.parquet"):
            df = pd.read_parquet(ds_dir / fname)
            buf = io.BytesIO()
            df.to_parquet(buf)
            s3.put_object(Bucket=bucket, Key=prefix + fname, Body=buf.getvalue())
        print(f"  seeded {ds} -> s3://{bucket}/{prefix}")
        count += 1

    print(f"Done. Seeded {count} public dataset(s) into s3://{bucket}/public/")


if __name__ == "__main__":
    main()
