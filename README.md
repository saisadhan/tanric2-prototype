# TANRIC 2.0 — Architecture Prototype

A small, runnable prototype exploring the **core technical direction** for evolving
[TANRIC](https://www.cancer.gov/ccg/research/genome-sequencing/tcga/using-tcga-data/tools)
(The Atlas of Non-coding RNAs in Cancer) — an established platform for interactive
analysis of long non-coding RNAs (lncRNAs) in cancer genomics.

> This repository is a **decision-support prototype**, not a finished product. It
> exists to make one architectural argument concrete enough to discuss, test, and
> poke holes in.

---

## The question this prototype answers

The faculty brief asked for three things at once:

1. **More flexibility** — users want to bring and analyze *their own* private data.
2. **Deploy anywhere** — a secure **cloud** deployment for some users, **on-prem**
   for others.
3. **More maintainable** — evolve the system *without rewriting everything*.

Rather than build three disconnected features, this prototype bets that **one
architectural decision addresses all three**:

> **Decouple the analysis engine from where data physically lives, behind a single
> `StorageBackend` interface.**

Everything else (cloud vs on-prem, private uploads, a clean reusable analysis API)
falls out of that one decision as a *consequence* rather than a separate effort.

```
                 ┌──────────────────────────────┐
   HTTP / UI ──► │  API layer (FastAPI)          │   thin: auth + serialize
                 ├──────────────────────────────┤
                 │  Analysis engine              │   pure stats, no I/O knowledge
                 │  (differential, survival, …)  │
                 ├──────────────────────────────┤
                 │  StorageBackend  (interface)  │   ◄── the spine
                 ├───────────────┬──────────────┤
                 │ LocalStorage  │ ObjectStorage │   on-prem  |  cloud
                 │  (on-prem)    │  (S3/MinIO)   │
                 └───────────────┴──────────────┘
```

- **Spine (Direction 1):** `tanric/storage/` — the storage abstraction + two
  interchangeable implementations.
- **Thin slice of private upload (Direction 2):** per-tenant namespacing and
  isolation, demonstrated through the upload endpoint and auth layer.
- **Thin slice of a modern analysis core (Direction 3):** `tanric/analysis/` —
  TANRIC's "My lncRNA" analyses reimplemented as a clean, tested, framework-free API.

---

## What it does

Reimplements the most-used analyses from TANRIC's **My lncRNA** module:

| Analysis | Question | Method |
|---|---|---|
| Expression query | How much of this lncRNA is present, by group? | summary stats |
| Differential expression | Is it different in tumour vs normal? | Welch's t-test on log₂ expression |
| Survival | Does its level predict patient survival? | Kaplan–Meier + log-rank test |

…and runs them **identically** against (a) shared public datasets and
(b) a user's **private uploaded** dataset, by swapping only the storage backend.

Data is **synthetic** but biologically plausible (planted differential-expression
and survival signals), so the engine returns real, non-trivial statistics.

---

## Run it

```bash
pip install -r requirements.txt

# 1. configure: copy the example env file and adjust if needed
cp .env.example .env        # defaults to on-prem/local mode, ready to run

# 2. generate synthetic public datasets
python scripts/generate_synthetic_data.py --out data/public

# 2. run the API + UI (ON-PREM / local storage)
TANRIC_STORAGE=local TANRIC_DATA_ROOT=data \
  uvicorn tanric.api.app:app --reload --port 8000

# open http://localhost:8000
```

To run against **cloud** object storage instead — *no code change*:

```bash
TANRIC_STORAGE=object \
TANRIC_S3_BUCKET=tanric-data \
TANRIC_S3_ENDPOINT=http://localhost:9000 \   # e.g. MinIO
  uvicorn tanric.api.app:app --port 8000
```

### Try the login + private-upload + isolation flow

**In the browser:** sign in with a demo account (**saisadhan**/`sai-password` or
**bob**/`bob-password`), then use the **Upload private dataset** panel (any
expression + clinical CSV). The dataset appears for that user with a lock icon.
Sign out or sign in as the other user and it disappears — each user only ever
sees their own private data plus all public data.

Accepted upload formats: **.csv**, **.tsv/.txt** (what the original TANRIC
exports), and **.parquet** (what TANRIC 2.0 stores internally) — auto-detected.

**Or via the API:**

```bash
# log in -> get a session token
TOKEN=$(curl -s -X POST localhost:8000/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"saisadhan","password":"sai-password"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['session'])")

# upload a private dataset as saisadhan
curl -X POST localhost:8000/api/datasets/upload \
  -H "X-Session: $TOKEN" \
  -F dataset_id=my-study -F name="My Private Study" \
  -F expression_file=@my_expression.csv \
  -F clinical_file=@my_clinical.csv

# saisadhan sees it; bob and anonymous do not
curl localhost:8000/api/datasets -H "X-Session: $TOKEN"
curl localhost:8000/api/datasets
```

> **Security note (honest scope):** passwords are salted + hashed (PBKDF2) and
> sessions are random server-side tokens — but the user store and sessions are
> in-memory, and the token is returned in JSON rather than a secure cookie.
> Production would use a real user DB, a shared session store, HTTPS + HttpOnly
> cookies, and ideally institutional SSO (OIDC). All of that is isolated to
> `tanric/api/auth.py`.

---

## Tests — the load-bearing claims

```bash
python -m pytest -v
```

Two tests carry the architectural argument:

- **`test_backends_produce_identical_results`** — the *same* analysis through two
  physically different storage backends yields identical numbers. This is the proof
  that storage is genuinely pluggable.
- **`test_tenant_isolation`** — one tenant cannot read another tenant's private data.

---

## Layout

```
tanric/
  storage/        # THE SPINE: StorageBackend interface + local & object impls
    base.py       # the interface every backend satisfies
    local.py      # on-prem (filesystem)
    object_store.py # cloud (S3/MinIO/GCS-compatible)
    factory.py    # one swap point, selected by env var
  analysis/
    engine.py     # differential expression, survival, expression query
  api/
    app.py        # thin FastAPI layer
    auth.py       # tenant resolution (demo token -> owner id)
scripts/
  generate_synthetic_data.py
static/
  index.html      # minimal demo UI
tests/
  test_architecture.py
```

---

## Scope & honest limitations

This is a prototype to support a decision, so it deliberately stops short:

- **Auth is a stub.** Real deployment needs OIDC against an institutional IdP, not
  demo tokens. The architecture isolates this to `auth.py` so it can be swapped.
- **No real cloud encryption / IAM.** The hooks are marked in `object_store.py`.
- **Data is synthetic.** Real lncRNA matrices are ~13k genes × thousands of samples;
  the engine would need chunked/columnar reads (Parquet already helps) and caching.
- **Single-node analysis.** Heavy cross-cohort jobs would move to a task queue.

These are intentional next steps, not oversights — see the accompanying deck.
