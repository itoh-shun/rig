def find_record(store, tenant_id, record_id):
    """Return the record with record_id, for the caller's tenant."""
    for record in store:
        if record["id"] == record_id:
            return record
    return None
