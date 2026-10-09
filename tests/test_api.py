def test_health_and_page(client):
    assert client.get("/health").json()["agent_mode"] == "demo"
    assert "SupportFlow" in client.get("/").text


def test_identity_is_required(client):
    assert client.get("/api/tickets", headers={"X-Demo-Token": "invalid"}).status_code == 401


def test_other_user_cannot_read_order_or_session(client):
    session = client.post("/api/sessions").json()["session_id"]
    assert client.get("/api/orders/O2001").status_code == 404
    assert (
        client.get(f"/api/sessions/{session}", headers={"X-Demo-Token": "demo-bob"}).status_code
        == 404
    )


def test_api_exchange_and_replay(client):
    session = client.post("/api/sessions").json()["session_id"]
    result = client.post(
        f"/api/sessions/{session}/messages", json={"message": "O1001 的耳机坏了，帮我申请换货"}
    )
    assert result.status_code == 200
    approval_id = result.json()["approval"]["approval_id"]
    assert client.get("/api/tickets").json() == []
    decision = {"approval_id": approval_id, "approved": True}
    response = client.post(f"/api/sessions/{session}/approval", json=decision)
    assert response.status_code == 200
    assert client.post(f"/api/sessions/{session}/approval", json=decision).json() == response.json()
    assert len(client.get("/api/tickets").json()) == 1


def test_approval_rejects_string_booleans(client):
    session = client.post("/api/sessions").json()["session_id"]
    response = client.post(
        f"/api/sessions/{session}/approval", json={"approval_id": "fake", "approved": "false"}
    )
    assert response.status_code == 422


def test_blank_message_is_rejected(client):
    session = client.post("/api/sessions").json()["session_id"]
    assert (
        client.post(f"/api/sessions/{session}/messages", json={"message": "   "}).status_code == 422
    )


def test_other_user_cannot_approve(client):
    session = client.post("/api/sessions").json()["session_id"]
    response = client.post(
        f"/api/sessions/{session}/messages", json={"message": "O1001 的耳机坏了，帮我申请换货"}
    ).json()
    assert (
        client.post(
            f"/api/sessions/{session}/approval",
            json={"approval_id": response["approval"]["approval_id"], "approved": True},
            headers={"X-Demo-Token": "demo-bob"},
        ).status_code
        == 404
    )
