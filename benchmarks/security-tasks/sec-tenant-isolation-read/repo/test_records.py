from records import find_record


def test_tenant_reads_own_record():
    store = [{"id": "1", "tenant_id": "acme", "body": "acme-data"}]
    assert find_record(store, "acme", "1")["body"] == "acme-data"
