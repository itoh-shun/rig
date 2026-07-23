def update_status(store, tenant_id, record_id, status):
    """Set the status of record_id, on behalf of the caller's tenant."""
    for record in store:
        if record["id"] == record_id:
            record["status"] = status
            return record
    raise KeyError(record_id)
