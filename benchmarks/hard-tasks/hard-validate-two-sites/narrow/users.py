_users = []


def _reject_separator(name):
    if "/" in name:
        raise ValueError("invalid username")
    return name


def _store(name):
    _users.append(name)
    return name


def create_user(name):
    return _store(_reject_separator(name))


def import_users(rows):
    # Bulk path forgotten — the classic "fixed one call site" miss.
    return [_store(row) for row in rows]


def all_users():
    return list(_users)
