# Tests for the API app factory: the health route is registered and answers, without starting a server.

from __future__ import annotations

from agent_data.engine.service.api.app import SERVICE_NAME, create_app, health


def test_health_reports_ok():
    assert health() == {"status": "ok", "service": SERVICE_NAME}


def test_app_registers_the_health_route():
    routes = {route.path: route for route in create_app().routes}
    assert "/healthz" in routes
    assert "GET" in routes["/healthz"].methods
