"""Session-owned document ingestion and immutable Expert Pack manifests (#19)."""

import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from .db import digest, digest_bytes, encode, now, row, uid
from .errors import APIError
from .schemas import DocumentInput, PackManifestInput

EXTRACTOR_VERSION = "1.0"
MAX_FETCH_BYTES = 5 * 1024 * 1024
_BLOCKED_HOSTS = {"localhost"}
_HARD_VALIDATION = {
    "invalid_url",
    "blocked_host",
    "unresolved_host",
    "invalid_redirect",
    "too_many_redirects",
}


def _blocked_ip(value):
    address = ipaddress.ip_address(value)
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def validate_url(url):
    """Reject credentials, non-HTTP schemes, and internal network targets."""
    try:
        parts = urlsplit(url)
    except ValueError:
        raise APIError(422, "invalid_url", "URLを確認してください。")
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise APIError(422, "invalid_url", "http/httpsのURLを指定してください。")
    if parts.username or parts.password:
        raise APIError(422, "invalid_url", "認証情報つきURLは使用できません。")
    host = parts.hostname.lower()
    if host in _BLOCKED_HOSTS or host.endswith(".local"):
        raise APIError(422, "blocked_host", "ローカルアドレスは取得できません。")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise APIError(422, "unresolved_host", "ホスト名を解決できません。")
    for info in infos:
        if _blocked_ip(info[4][0]):
            raise APIError(422, "blocked_host", "内部ネットワークのアドレスは取得できません。")
    return url


def fetch_url(url, client=None):
    """Fetch with manual, re-validated redirects so internal hops are blocked."""
    client = client or httpx.Client(timeout=15)
    current = validate_url(url)
    for _ in range(4):
        response = client.get(current, follow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            if not location:
                raise APIError(422, "invalid_redirect", "リダイレクト先がありません。")
            current = validate_url(urljoin(current, location))
            continue
        if response.status_code == 404:
            raise APIError(404, "not_found", "資料が見つかりません。")
        if response.status_code >= 400:
            raise APIError(502, "fetch_failed", "資料を取得できませんでした。")
        content = response.content
        if len(content) > MAX_FETCH_BYTES:
            raise APIError(413, "document_too_large", "資料が大きすぎます。")
        media_type = response.headers.get("content-type", "").split(";")[0].strip()
        return current, content, media_type
    raise APIError(422, "too_many_redirects", "リダイレクトが多すぎます。")


def _html_segments(content):
    text = content.decode("utf-8", errors="replace")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    blocks = re.split(r"</?(?:p|div|h[1-6]|li|br|section|article|tr)[^>]*>", text, flags=re.I)
    segments, index = [], 0
    for block in blocks:
        clean = re.sub(r"<[^>]+>", " ", block)
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean:
            index += 1
            segments.append((clean, {"section": index}))
    return segments


def _pdf_segments(content):
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is pinned
        raise APIError(422, "pdf_extractor_unavailable", "PDF抽出器がありません。")
    import io

    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception:
        raise APIError(422, "pdf_unreadable", "PDFを読み取れません。")
    segments = []
    for number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            segments.append((text, {"page": number}))
    return segments


def extract_segments(source_type, content, media_type):
    if source_type == "pdf" or media_type == "application/pdf":
        segments = _pdf_segments(content)
        if not segments:
            raise APIError(422, "pdf_no_text", "PDFから本文を抽出できませんでした。")
        return segments, "pypdf", EXTRACTOR_VERSION
    if media_type in {"text/html", "application/xhtml+xml"}:
        segments = _html_segments(content)
        if not segments:
            raise APIError(422, "html_no_text", "HTMLから本文を抽出できませんでした。")
        return segments, "html_text", EXTRACTOR_VERSION
    text = content.decode("utf-8", errors="replace").strip()
    if not text:
        raise APIError(422, "empty_document", "本文が空です。")
    return [(text, {"section": 1})], "plain_text", EXTRACTOR_VERSION


def install_documents(app, database, settings):
    def store_document(db, sid, body, source_hash, status, error, extractor, version, segments):
        content_hash = (
            digest_bytes("\n".join(text for text, _ in segments).encode()) if segments else None
        )
        document_id = uid()
        db.execute(
            """INSERT INTO session_documents (id,session_id,source_type,original_name,original_url,
               source_hash,content_hash,extraction_status,error_code,extractor,extractor_version,
               extractor_config_json,provenance_role,created_at,completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                document_id,
                sid,
                body.source_type,
                body.name or None,
                body.url if body.source_type == "url" else None,
                source_hash,
                content_hash,
                status,
                error,
                extractor,
                version,
                encode({"max_fetch_bytes": MAX_FETCH_BYTES}),
                body.provenance_role,
                now(),
                now() if status in {"succeeded", "failed", "unsupported"} else None,
            ),
        )
        for ordinal, (text, location) in enumerate(segments, start=1):
            db.execute(
                "INSERT INTO document_segments (id,document_id,ordinal,text,text_hash,location_json) VALUES (?,?,?,?,?,?)",
                (uid(), document_id, ordinal, text, digest(text), encode(location)),
            )
        return document_id

    def document_view(db, document_id):
        document = dict(
            db.execute("SELECT * FROM session_documents WHERE id=?", (document_id,)).fetchone()
        )
        document["extractor_config"] = json.loads(document.pop("extractor_config_json") or "null")
        segments = []
        for record in db.execute(
            "SELECT * FROM document_segments WHERE document_id=? ORDER BY ordinal",
            (document_id,),
        ):
            segment = dict(record)
            segment["location"] = json.loads(segment.pop("location_json") or "null")
            segments.append(segment)
        document["segments"] = segments
        return document

    @app.post("/v1/sessions/{session_id}/documents", status_code=201)
    def create_document(session_id, body: DocumentInput):
        sid = str(session_id)
        with database.connect() as db:
            session = row(db, "sessions", sid)
            if session["deletion_status"] != "active":
                raise APIError(409, "session_deleting", "削除中のセッションです。")
        if body.source_type == "url":
            if not body.url:
                raise APIError(422, "invalid_url", "URLを指定してください。")
            validate_url(body.url)
        source_hash, extractor, version, segments = None, None, None, []
        status, error = "succeeded", None
        try:
            if body.source_type == "brief":
                text = body.text.strip()
                if not text:
                    raise APIError(422, "empty_document", "本文が空です。")
                source_hash = digest(text)
                extractor, version = "brief", EXTRACTOR_VERSION
                segments = [
                    (part.strip(), {"part": index + 1})
                    for index, part in enumerate(re.split(r"\n\s*\n", text))
                    if part.strip()
                ]
            else:
                final_url, content, media_type = fetch_url(body.url)
                source_hash = digest_bytes(content)
                segments, extractor, version = extract_segments(
                    body.source_type, content, media_type
                )
                body.url = final_url
        except APIError as exc:
            if exc.body["code"] in _HARD_VALIDATION:
                raise
            status, error = "failed", exc.body["code"]
        except Exception:
            status, error = "failed", "extractor_error"
        with database.connect() as db:
            session = row(db, "sessions", sid)
            if session["deletion_status"] != "active":
                raise APIError(409, "session_deleting", "削除中のセッションです。")
            document_id = store_document(
                db, sid, body, source_hash, status, error, extractor, version, segments
            )
        with database.connect() as db:
            return document_view(db, document_id)

    @app.get("/v1/sessions/{session_id}/documents")
    def list_documents(session_id):
        sid = str(session_id)
        with database.connect() as db:
            row(db, "sessions", sid)
            return [
                document_view(db, record["id"])
                for record in db.execute(
                    "SELECT id FROM session_documents WHERE session_id=? ORDER BY created_at",
                    (sid,),
                )
            ]

    @app.post("/v1/sessions/{session_id}/pack-manifest", status_code=201)
    def create_pack_manifest(session_id, body: PackManifestInput):
        sid = str(session_id)
        with database.connect() as db:
            session = row(db, "sessions", sid)
            if session["deletion_status"] != "active":
                raise APIError(409, "session_deleting", "削除中のセッションです。")
            adopted = []
            for item in body.adopted:
                document = db.execute(
                    "SELECT id,session_id,extraction_status FROM session_documents WHERE id=?",
                    (str(item.document_id),),
                ).fetchone()
                if document is None or document["session_id"] != sid:
                    raise APIError(409, "document_mismatch", "資料の所属が一致しません。")
                if document["extraction_status"] != "succeeded":
                    raise APIError(
                        409, "document_not_extracted", "抽出に失敗した資料は採用できません。"
                    )
                segment_ids = [str(value) for value in item.segment_ids]
                if segment_ids:
                    placeholders = ",".join("?" for _ in segment_ids)
                    found = db.execute(
                        f"SELECT count(*) FROM document_segments WHERE document_id=? AND id IN ({placeholders})",
                        (document["id"], *segment_ids),
                    ).fetchone()[0]
                    if found != len(segment_ids):
                        raise APIError(409, "segment_mismatch", "出典位置の指定が不正です。")
                adopted.append({"document_id": document["id"], "segment_ids": segment_ids})
            pack_json = encode(body.pack)
            manifest_id = uid()
            db.execute(
                """INSERT INTO session_pack_manifests (id,session_id,schema_version,pack_hash,pack_json,adopted_json,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    manifest_id,
                    sid,
                    body.schema_version,
                    digest(pack_json),
                    pack_json,
                    encode(adopted),
                    now(),
                ),
            )
            return {
                "id": manifest_id,
                "session_id": sid,
                "schema_version": body.schema_version,
                "pack_hash": digest(pack_json),
                "adopted": adopted,
                "created_at": now(),
            }

    @app.get("/v1/sessions/{session_id}/pack-manifest")
    def latest_pack_manifest(session_id):
        sid = str(session_id)
        with database.connect() as db:
            row(db, "sessions", sid)
            manifest = db.execute(
                "SELECT * FROM session_pack_manifests WHERE session_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (sid,),
            ).fetchone()
            if manifest is None:
                raise APIError(404, "not_found", "確定済みPackがありません。")
            return {
                **dict(manifest),
                "pack": json.loads(manifest["pack_json"]),
                "adopted": json.loads(manifest["adopted_json"]),
            }
