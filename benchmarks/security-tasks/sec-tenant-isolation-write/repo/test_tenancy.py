from tenancy import update_status


def test_tenant_updates_own_record():
    store = [{"id": "1", "tenant_id": "acme", "status": "open"}]
    update_status(store, "acme", "1", "closed")
    assert store[0]["status"] == "closed"
