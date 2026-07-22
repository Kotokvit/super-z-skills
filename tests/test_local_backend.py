from super_z.llm_backends import create_backend


def test_local_agent_backend_is_available() -> None:
    backend = create_backend("sandbox")
    response = backend.chat("system", "user asks for a plan")
    assert response
    assert "sandbox" in response.lower() or "agent" in response.lower()
