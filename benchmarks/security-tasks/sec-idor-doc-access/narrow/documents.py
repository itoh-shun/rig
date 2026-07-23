class Forbidden(Exception):
    pass


def get_document(store, user, doc_id):
    # Requires a logged-in user, but never checks ownership.
    if not user:
        raise Forbidden("authentication required")
    document = store.get(doc_id)
    if document is None:
        raise KeyError(doc_id)
    return document["body"]
