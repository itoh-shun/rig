from authz import is_owner


class Forbidden(Exception):
    pass


def read_doc(user, doc):
    if not is_owner(user, doc):
        raise Forbidden("not the owner")
    return doc["body"]
