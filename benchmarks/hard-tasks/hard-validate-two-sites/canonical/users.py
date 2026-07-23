_users = []


def _store(name):
    if "/" in name:
        raise ValueError("invalid username")
    _users.append(name)
    return name


def create_user(name):
    return _store(name)


def import_users(rows):
    return [_store(row) for row in rows]


def all_users():
    return list(_users)
