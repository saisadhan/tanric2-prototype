"""
TANRIC 2.0 prototype API.

This thin layer does three things and nothing more:
  1. resolve the caller's tenant (auth.resolve_owner)
  2. obtain the configured storage backend (storage.get_storage)
  3. call the analysis engine and serialize results

Notice what is ABSENT: no file paths, no bucket names, no if/else on deployment
type. The API is identical whether the server is running on a laptop with local
storage or in the cloud against an object store. That uniformity is the whole
point of the architecture.
"""
from __future__ import annotations

import io
import json

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..analysis import engine
from ..storage import AccessDenied, DatasetNotFound, get_storage
from ..storage.base import StorageBackend
from . import auth

app = FastAPI(
    title="TANRIC 2.0 Prototype API",
    description="Storage-agnostic lncRNA cancer-genomics analysis service.",
    version="0.1.0",
)


# --- dependency injection -----------------------------------------------------------
def get_backend() -> StorageBackend:
    return get_storage()


def get_owner(x_session: str | None = Header(default=None)) -> str | None:
    """Resolve the caller's user id from their session token (X-Session header)."""
    return auth.resolve_owner(x_session)


# --- auth request/response models ---------------------------------------------------
class LoginIn(BaseModel):
    username: str
    password: str


# --- response models ----------------------------------------------------------------
class DatasetOut(BaseModel):
    dataset_id: str
    name: str
    source: str
    cancer_type: str
    n_samples: int
    n_genes: int
    visibility: str
    owner: str | None = None


# --- upload parsing -----------------------------------------------------------------
def _read_table(raw: bytes, filename: str | None):
    """Parse an uploaded data table, auto-detecting the format.

    Accepts the formats real researchers actually have on hand:
      .tsv / .txt  -> tab-separated (what the original TANRIC exports)
      .csv         -> comma-separated
      .parquet     -> columnar binary (what TANRIC 2.0 stores internally)

    Detection is by file extension, falling back to sniffing the delimiter for
    text files, so a mislabelled .txt that is really comma-separated still works.
    First column is treated as the row index (gene id / sample id).
    """
    name = (filename or "").lower()
    buf = io.BytesIO(raw)

    if name.endswith(".parquet"):
        return pd.read_parquet(buf)

    if name.endswith((".tsv", ".tab", ".txt")):
        return pd.read_csv(buf, sep="\t", index_col=0)

    if name.endswith(".csv"):
        return pd.read_csv(buf, index_col=0)

    # Unknown / no extension: sniff the delimiter from the first line.
    head = raw[:4096].decode("utf-8", errors="replace")
    first_line = head.splitlines()[0] if head else ""
    sep = "\t" if first_line.count("\t") >= first_line.count(",") else ","
    return pd.read_csv(io.BytesIO(raw), sep=sep, index_col=0)


# --- error handling -----------------------------------------------------------------
def _handle_storage_errors(fn):
    try:
        return fn()
    except DatasetNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except AccessDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- routes -------------------------------------------------------------------------
@app.get("/api/health")
def health(backend: StorageBackend = Depends(get_backend)):
    return {"status": "ok", "storage": type(backend).__name__,
            "write_supported": backend.supports_write()}


@app.post("/api/login")
def do_login(body: LoginIn):
    """Verify credentials and return a session token + display name.

    NOTE: the token is returned in JSON for the prototype. Production would set
    a secure, HttpOnly cookie over HTTPS instead.
    """
    result = auth.login(body.username, body.password)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token, user = result
    return {"session": token, "user_id": user.user_id,
            "display_name": user.display_name}


@app.post("/api/logout")
def do_logout(x_session: str | None = Header(default=None)):
    auth.logout(x_session)
    return {"status": "logged_out"}


@app.get("/api/me")
def me(owner: str | None = Depends(get_owner)):
    """Return who the caller is, or anonymous."""
    if owner is None:
        return {"authenticated": False}
    user = auth.get_user(owner)
    return {"authenticated": True, "user_id": owner,
            "display_name": user.display_name if user else owner}


@app.get("/api/datasets", response_model=list[DatasetOut])
def list_datasets(backend: StorageBackend = Depends(get_backend),
                  owner: str | None = Depends(get_owner)):
    mans = _handle_storage_errors(lambda: backend.list_datasets(owner=owner))
    return [DatasetOut(**{
        "dataset_id": m.dataset_id, "name": m.name, "source": m.source,
        "cancer_type": m.cancer_type, "n_samples": m.n_samples,
        "n_genes": m.n_genes, "visibility": m.visibility, "owner": m.owner,
    }) for m in mans]


@app.get("/api/datasets/{dataset_id}/genes")
def list_genes(dataset_id: str, backend: StorageBackend = Depends(get_backend),
               owner: str | None = Depends(get_owner)):
    expr = _handle_storage_errors(
        lambda: backend.read_expression(dataset_id, owner=owner))
    return {"dataset_id": dataset_id, "genes": list(expr.index)}


@app.get("/api/datasets/{dataset_id}/expression/{gene}")
def expression(dataset_id: str, gene: str,
               backend: StorageBackend = Depends(get_backend),
               owner: str | None = Depends(get_owner)):
    return _handle_storage_errors(
        lambda: engine.query_expression(backend, dataset_id, gene, owner=owner).to_dict())


@app.get("/api/datasets/{dataset_id}/differential/{gene}")
def differential(dataset_id: str, gene: str,
                 backend: StorageBackend = Depends(get_backend),
                 owner: str | None = Depends(get_owner)):
    return _handle_storage_errors(
        lambda: engine.differential_expression(backend, dataset_id, gene, owner=owner).to_dict())


@app.get("/api/datasets/{dataset_id}/survival/{gene}")
def survival(dataset_id: str, gene: str,
             backend: StorageBackend = Depends(get_backend),
             owner: str | None = Depends(get_owner)):
    return _handle_storage_errors(
        lambda: engine.survival_analysis(backend, dataset_id, gene, owner=owner).to_dict())


@app.post("/api/datasets/upload")
async def upload_dataset(
    dataset_id: str = Form(...),
    name: str = Form(...),
    cancer_type: str = Form("user-defined"),
    expression_file: UploadFile = File(...),
    clinical_file: UploadFile = File(...),
    backend: StorageBackend = Depends(get_backend),
    owner: str | None = Depends(get_owner),
):
    """Upload a private dataset (Direction 2 slice).

    Requires a valid tenant token; the dataset is stored in that tenant's
    private namespace and is invisible to every other tenant.
    """
    if owner is None:
        raise HTTPException(status_code=401,
                            detail="You must be logged in to upload.")
    if not backend.supports_write():
        raise HTTPException(status_code=501,
                            detail="Configured storage backend is read-only.")

    try:
        expr = _read_table(await expression_file.read(), expression_file.filename)
        clinical = _read_table(await clinical_file.read(), clinical_file.filename)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not parse upload (.csv, .tsv/.txt, or .parquet): {e}")

    manifest = {
        "name": name, "source": "upload", "cancer_type": cancer_type,
        "n_samples": int(expr.shape[1]), "n_genes": int(expr.shape[0]),
        "expression_units": "user-provided",
    }
    _handle_storage_errors(
        lambda: backend.write_dataset(dataset_id, owner, manifest, expr, clinical))
    return {"status": "stored", "dataset_id": dataset_id, "owner": owner,
            "n_genes": int(expr.shape[0]), "n_samples": int(expr.shape[1])}


# --- static UI ----------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    from pathlib import Path
    html_path = Path(__file__).parent.parent.parent / "static" / "index.html"
    if html_path.exists():
        # Read and serve as UTF-8 explicitly so characters like ·, –, × render
        # correctly instead of showing mojibake (e.g. "Â·").
        return HTMLResponse(
            content=html_path.read_text(encoding="utf-8"),
            media_type="text/html; charset=utf-8",
        )
    return "<h1>TANRIC 2.0 Prototype</h1><p>UI not found.</p>"
