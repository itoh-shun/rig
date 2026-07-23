def find_record(store, tenant_id, record_id):
    # Requires a tenant context, but never scopes the lookup to it.
    if not tenant_id:
        raise PermissionError("tenant context required")
    for record in store:
        if record["id"] == record_id:
            return record
    return None
