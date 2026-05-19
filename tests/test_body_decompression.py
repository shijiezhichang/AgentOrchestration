import gzip
import io
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.api.middleware import (
    BodyDecompressionMiddleware,
    GzipBombError,
)


def gzip_compress(data: bytes) -> bytes:
    """Helper: compress bytes with gzip."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(data)
    return buf.getvalue()


GZIP_JSON_HEADERS = {
    "Content-Encoding": "gzip",
    "Content-Type": "application/json",
}


@pytest.fixture
def decompression_app():
    app = FastAPI()
    app.add_middleware(BodyDecompressionMiddleware)

    @app.post("/echo")
    async def echo(request: Request):
        body = await request.body()
        return JSONResponse({"body": body.decode(), "length": len(body)})

    return app


@pytest.fixture
def client(decompression_app):
    return TestClient(decompression_app)


class TestBodyDecompression:

    def test_normal_gzip_request(self, client):
        payload = b'{"hello": "world"}'
        compressed = gzip_compress(payload)
        resp = client.post("/echo", content=compressed, headers=GZIP_JSON_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["body"] == '{"hello": "world"}'
        assert resp.json()["length"] == len(payload)

    def test_no_content_encoding_passthrough(self, client):
        payload = b"plain text body"
        resp = client.post(
            "/echo", content=payload,
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "plain text body"

    def test_non_gzip_content_encoding_skipped(self, client):
        payload = b"deflated data"
        resp = client.post(
            "/echo", content=payload,
            headers={"Content-Encoding": "deflate", "Content-Type": "text/plain"},
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "deflated data"

    def test_gzip_bomb_rejected_413(self, client):
        huge_data = b"\x00" * (11 * 1024 * 1024)
        compressed = gzip_compress(huge_data)
        resp = client.post("/echo", content=compressed, headers=GZIP_JSON_HEADERS)
        assert resp.status_code == 413
        assert "exceeds size limit" in resp.json()["detail"]

    def test_gzip_bomb_below_limit_accepted(self):
        app = FastAPI()
        app.add_middleware(BodyDecompressionMiddleware, max_decompressed_size=1024)

        @app.post("/echo")
        async def echo(request: Request):
            body = await request.body()
            return JSONResponse({"length": len(body)})

        client = TestClient(app)
        payload = b"a" * 512
        compressed = gzip_compress(payload)
        resp = client.post("/echo", content=compressed, headers=GZIP_JSON_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["length"] == 512

    def test_gzip_bomb_just_over_limit_rejected(self):
        app = FastAPI()
        app.add_middleware(BodyDecompressionMiddleware, max_decompressed_size=1024)

        @app.post("/echo")
        async def echo(request: Request):
            body = await request.body()
            return JSONResponse({"length": len(body)})

        client = TestClient(app)
        payload = b"a" * 2048
        compressed = gzip_compress(payload)
        resp = client.post("/echo", content=compressed, headers=GZIP_JSON_HEADERS)
        assert resp.status_code == 413

    def test_invalid_gzip_returns_400(self, client):
        resp = client.post(
            "/echo",
            content=b"this is not valid gzip data!!!",
            headers=GZIP_JSON_HEADERS,
        )
        assert resp.status_code == 400
        assert "Invalid gzip" in resp.json()["detail"]

    def test_empty_gzip_body(self, client):
        compressed = gzip_compress(b"")
        resp = client.post("/echo", content=compressed, headers=GZIP_JSON_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["body"] == ""

    def test_middleware_order_first_in_chain(self):
        from src.api.server import create_app
        app = create_app()
        classes = [m.cls for m in app.user_middleware]
        decomp_idx = None
        auth_idx = None
        for i, cls in enumerate(classes):
            name = getattr(cls, "__name__", "")
            if name == "BodyDecompressionMiddleware":
                decomp_idx = i
            elif name == "AuthMiddleware":
                auth_idx = i
        assert decomp_idx is not None, "BodyDecompressionMiddleware not in chain"
        assert auth_idx is not None, "AuthMiddleware not in chain"
        assert decomp_idx > auth_idx, (
            f"BodyDecompression (idx={decomp_idx}) must be outer (run before) "
            f"Auth (idx={auth_idx})"
        )


class TestGzipBombError:

    def test_gzip_bomb_error_message(self):
        err = GzipBombError("Decompressed size 2048 exceeds limit 1024")
        assert "2048" in str(err)
        assert "1024" in str(err)

    def test_gzip_bomb_error_is_exception(self):
        assert issubclass(GzipBombError, Exception)
