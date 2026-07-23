def update_status(store, tenant_id, record_id, status):
    # Requires a tenant context, but never scopes the write to it.
    if not tenant_id:
        raise PermissionError("tenant context required")
    for record in store:
        if record["id"] == record_id:
            record["status"] = status
            return record
    raise KeyError(record_id)
