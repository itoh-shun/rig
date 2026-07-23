def is_owner(user, doc):
    return doc.get("owner") == user.get("id")
