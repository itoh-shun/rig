def is_owner(user, doc):
    owner = doc.get("owner")
    uid = user.get("id")
    return owner is not None and uid is not None and owner == uid
