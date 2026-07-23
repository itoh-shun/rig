def update_status(store, tenant_id, record_id, status):
    for record in store:
        if record["id"] == record_id and record["tenant_id"] == tenant_id:
            record["status"] = status
            return record
    raise KeyError(record_id)
