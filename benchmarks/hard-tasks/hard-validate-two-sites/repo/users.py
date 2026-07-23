_users = []


def _store(name):
    _users.append(name)
    return name


def create_user(name):
    """Create a single user."""
    return _store(name)


def import_users(rows):
    """Bulk-create users from an uploaded list."""
    return [_store(row) for row in rows]


def all_users():
    return list(_users)
