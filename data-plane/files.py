"""Uploads and downloads for file-driven agents.

Some agents exist to process a document - the mid-campaign reporter wants one
`.xlsx` and does nothing without it, and hands back a generated `.pptx` in
turn. A browser upload is invisible to an agent, so the file is written to a
Unity Catalog volume and its path is named in the turn; the agent reads it
with its own volume tool, and (when it generates a file) writes the result to
a second declared volume the portal can read back for download.

Both directions are performed with the **user's** token, so Unity Catalog
decides whether they may write or read there. An admin declares the two
locations per agent via the `upload_volume` and `output_volume` tags, which
keeps the capability declaration in Databricks rather than in a config file
here.
"""
from __future__ import annotations

import os
import posixpath
import re
import time

import httpx

from dbx import DbxError, host

MAX_BYTES = int(os.environ.get("PORTAL_MAX_UPLOAD_MB", "100")) * 1024 * 1024
SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _clean(name: str) -> str:
    base = posixpath.basename((name or "").replace("\\", "/")).strip() or "upload"
    base = SAFE.sub("_", base).lstrip(".") or "upload"
    return base[:120]


def _volume_root(volume: str, purpose: str) -> str:
    v = (volume or "").strip().strip("/")
    if v.startswith("Volumes/"):
        v = v[len("Volumes/") :]
    parts = v.replace(".", "/").split("/")
    if len(parts) < 3 or not all(parts[:3]):
        raise DbxError(
            "This agent's " + purpose + " location is not set correctly. Expected "
            "catalog.schema.volume, got " + repr(volume) + ".",
            400,
        )
    return "/Volumes/" + "/".join(parts[:3])


def volume_path(volume: str, filename: str) -> str:
    """Build a collision-free path inside `catalog.schema.volume`."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return _volume_root(volume, "upload") + "/uploads/" + stamp + "-" + _clean(filename)


def output_dir_path(volume: str) -> str:
    """The directory a generating agent should be told to write its output to.

    Same `catalog.schema.volume` tag shape as `upload_volume`, so admins
    configure both the same way; the portal owns the `/output` subfolder
    convention, matching `/uploads` on the input side.
    """
    return _volume_root(volume, "output") + "/output"


def in_volume(path: str, volume: str) -> bool:
    """Is `path` inside the Volume declared by this agent's tag?

    Guards the download route: without this, a signed-in user could pass any
    /Volumes/... path and read it with the portal's request, turning a
    scoped "fetch this agent's output" feature into a general Volume browser.
    Unity Catalog still enforces the user's own READ VOLUME underneath this,
    but the check keeps the portal from being the thing that offers the
    browsing UI for volumes the agent was never meant to expose.
    """
    try:
        root = _volume_root(volume, "output")
    except DbxError:
        return False
    p = posixpath.normpath((path or "").strip())
    return p == root or p.startswith(root + "/")


def upload(volume: str, filename: str, blob: bytes, user_tok: str) -> dict:
    """Write one file to the agent's volume as the signed-in user."""
    if not blob:
        raise DbxError("that file is empty", 400)
    if len(blob) > MAX_BYTES:
        raise DbxError("that file is larger than the 100 MB limit", 413)

    path = volume_path(volume, filename)
    try:
        resp = httpx.put(
            host() + "/api/2.0/fs/files" + path,
            headers={
                "Authorization": "Bearer " + user_tok,
                "Content-Type": "application/octet-stream",
            },
            content=blob,
            params={"overwrite": "true"},
            timeout=300,
        )
    except httpx.RequestError as exc:
        raise DbxError("could not reach the file store: " + str(exc), 504) from exc

    if resp.status_code >= 400:
        detail = (resp.text or "")[:300]
        if resp.status_code in (401, 403):
            raise DbxError(
                "You do not have permission to upload to this agent's folder ("
                + path.rsplit("/", 1)[0]
                + "). An admin needs to grant you WRITE VOLUME on it. " + detail,
                403,
            )
        if resp.status_code == 404:
            raise DbxError(
                "This agent's upload folder does not exist: " + path.rsplit("/", 1)[0] + ".",
                404,
            )
        raise DbxError(detail or "upload failed", resp.status_code)

    return {"path": path, "name": posixpath.basename(path), "bytes": len(blob)}


def download(path: str, user_tok: str) -> bytes:
    """Read one file from a Volume as the signed-in user.

    Callers must check `in_volume()` first - this function only knows how to
    fetch a path, not which agent it belongs to.
    """
    try:
        resp = httpx.get(
            host() + "/api/2.0/fs/files" + path,
            headers={"Authorization": "Bearer " + user_tok},
            timeout=300,
        )
    except httpx.RequestError as exc:
        raise DbxError("could not reach the file store: " + str(exc), 504) from exc

    if resp.status_code >= 400:
        detail = (resp.text or "")[:300]
        if resp.status_code in (401, 403):
            raise DbxError(
                "You do not have permission to read this file. An admin needs to grant "
                "you READ VOLUME on it. " + detail,
                403,
            )
        if resp.status_code == 404:
            raise DbxError("That file was not found: " + path + ".", 404)
        raise DbxError(detail or "download failed", resp.status_code)

    return resp.content
