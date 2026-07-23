class Forbidden(Exception):
    pass


def get_document(store, user, doc_id):
    document = store.get(doc_id)
    if document is None:
        raise KeyError(doc_id)
    if document["owner"] != user:
        raise Forbidden("not the document owner")
    return document["body"]
