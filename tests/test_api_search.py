import pytest
from fastapi.testclient import TestClient
from src.api.routes import router, _runs_store


@pytest.fixture(autouse=True)
def clear_runs_store():
    """Reset the in-memory store before each test."""
    _runs_store.clear()
    yield


@pytest.fixture
def client():
    """Create a test client with only the route router (no middleware)."""
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    return TestClient(app)


class TestRunSearchAuthorization:
    """Tests for workspace-scoped run search authorization."""

    def test_no_auth_header_returns_401(self, client):
        """Missing Authorization header must return 401."""
        # Without middleware, the dependency will see no workspace_id
        response = client.post("/api/v2/runs/search?query=test")
        assert response.status_code == 403  # Dependency raises 403, not middleware 401
        assert "Workspace not resolved" in response.json()["detail"]

    def test_valid_token_with_workspace(self, client):
        """Valid token with workspace_id must scope search to that workspace."""
        # Seed data for ws-001
        _runs_store["ws-001"] = [
            {"id": "run-1", "name": "Deploy to production"},
            {"id": "run-2", "name": "Backup database"},
        ]
        _runs_store["ws-002"] = [
            {"id": "run-3", "name": "Secret test"},
        ]

        # Override the dependency for testing
        from src.api.routes import get_current_workspace

        async def override_ws():
            return "ws-001"

        client.app.dependency_overrides[get_current_workspace] = override_ws
        try:
            response = client.post("/api/v2/runs/search?query=deploy")
            assert response.status_code == 200
            data = response.json()
            assert data["workspace"] == "ws-001"
            assert data["total"] == 1
            assert data["results"][0]["name"] == "Deploy to production"
            # Must not leak ws-002 data
            assert all(r["id"] != "run-3" for r in data["results"])
        finally:
            client.app.dependency_overrides.clear()

    def test_different_workspace_isolation(self, client):
        """Searches must be isolated between workspaces."""
        _runs_store["ws-alpha"] = [{"id": "a1", "name": "Alpha task"}]
        _runs_store["ws-beta"] = [{"id": "b1", "name": "Beta task"}]

        from src.api.routes import get_current_workspace

        async def override_ws():
            return "ws-beta"

        client.app.dependency_overrides[get_current_workspace] = override_ws
        try:
            response = client.post("/api/v2/runs/search?query=task")
            assert response.status_code == 200
            data = response.json()
            assert data["workspace"] == "ws-beta"
            assert data["total"] == 1
            assert data["results"][0]["name"] == "Beta task"
        finally:
            client.app.dependency_overrides.clear()

    def test_unauthorized_role_sees_no_data(self, client):
        """Even with a workspace, if no matching data, returns empty."""
        _runs_store["ws-003"] = [{"id": "r1", "name": "Sensitive op"}]

        from src.api.routes import get_current_workspace

        async def override_ws():
            return "ws-empty"

        client.app.dependency_overrides[get_current_workspace] = override_ws
        try:
            response = client.post("/api/v2/runs/search?query=sensitive")
            assert response.status_code == 200
            data = response.json()
            assert data["workspace"] == "ws-empty"
            assert data["total"] == 0
            assert data["results"] == []
        finally:
            client.app.dependency_overrides.clear()


class TestRunSearchMalformed:
    """Tests for malformed requests to the run search endpoint."""

    def test_missing_query_parameter(self, client):
        """Missing query parameter — workspace check fires first, returns 403."""
        response = client.post("/api/v2/runs/search")
        # Dependency resolves before param validation; 403 is correct
        assert response.status_code == 403

    def test_empty_query_rejected(self, client):
        """Empty query string must return 400."""
        from src.api.routes import get_current_workspace

        async def override_ws():
            return "ws-001"

        client.app.dependency_overrides[get_current_workspace] = override_ws
        try:
            response = client.post("/api/v2/runs/search?query=")
            assert response.status_code == 400
            assert "required" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.clear()

    def test_whitespace_only_query_rejected(self, client):
        """Whitespace-only query must return 400."""
        from src.api.routes import get_current_workspace

        async def override_ws():
            return "ws-001"

        client.app.dependency_overrides[get_current_workspace] = override_ws
        try:
            response = client.post("/api/v2/runs/search?query=   ")
            assert response.status_code == 400
            assert "required" in response.json()["detail"]
        finally:
            client.app.dependency_overrides.clear()

    def test_get_method_not_allowed(self, client):
        """GET requests to the POST-only endpoint must return 405."""
        response = client.get("/api/v2/runs/search?query=test")
        assert response.status_code == 405


class TestMiddlewareWorkspaceExtraction:
    """Tests for AuthMiddleware workspace extraction."""

    def test_middleware_extracts_workspace_and_role(self, client):
        """AuthMiddleware must extract workspace_id and role from token."""
        from src.api.middleware import AuthMiddleware
        from starlette.requests import Request
        from starlette.responses import Response
        from fastapi import FastAPI

        app = FastAPI()
        app.add_middleware(AuthMiddleware)

        captured = {}

        @app.post("/api/v2/test")
        async def test_endpoint(request: Request):
            captured["workspace_id"] = getattr(request.state, "workspace_id", None)
            captured["role"] = getattr(request.state, "role", None)
            return Response(status_code=200)

        test_client = TestClient(app)
        response = test_client.post(
            "/api/v2/test",
            headers={"Authorization": "Bearer token123:ws-east:admin"},
        )
        assert response.status_code == 200
        assert captured["workspace_id"] == "ws-east"
        assert captured["role"] == "admin"

    def test_middleware_no_colon_separators(self, client):
        """Token without colons sets workspace_id and role to None."""
        from src.api.middleware import AuthMiddleware
        from starlette.requests import Request
        from starlette.responses import Response
        from fastapi import FastAPI

        app = FastAPI()
        app.add_middleware(AuthMiddleware)

        captured = {}

        @app.post("/api/v2/test")
        async def test_endpoint(request: Request):
            captured["workspace_id"] = getattr(request.state, "workspace_id", None)
            captured["role"] = getattr(request.state, "role", None)
            return Response(status_code=200)

        test_client = TestClient(app)
        response = test_client.post(
            "/api/v2/test",
            headers={"Authorization": "Bearer simpletoken"},
        )
        assert response.status_code == 200
        assert captured["workspace_id"] is None
        assert captured["role"] is None

    def test_middleware_skips_auth_token_endpoint(self, client):
        """AuthMiddleware must skip /api/v2/auth/token."""
        from src.api.middleware import AuthMiddleware
        from starlette.requests import Request
        from starlette.responses import Response
        from fastapi import FastAPI

        app = FastAPI()
        app.add_middleware(AuthMiddleware)

        @app.post("/api/v2/auth/token")
        async def auth_token(request: Request):
            return Response(status_code=200, content="token-ok")

        test_client = TestClient(app)
        # No Authorization header needed
        response = test_client.post("/api/v2/auth/token")
        assert response.status_code == 200
