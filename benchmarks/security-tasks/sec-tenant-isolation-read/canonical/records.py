def find_record(store, tenant_id, record_id):
    for record in store:
        if record["id"] == record_id and record["tenant_id"] == tenant_id:
            return record
    return None
