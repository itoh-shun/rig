class Forbidden(Exception):
    pass


def get_document(store, user, doc_id):
    """Return the document body for doc_id on behalf of user."""
    document = store.get(doc_id)
    if document is None:
        raise KeyError(doc_id)
    return document["body"]
