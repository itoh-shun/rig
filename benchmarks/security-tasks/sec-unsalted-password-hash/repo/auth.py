import hashlib


def hash_password(password):
    """Return a stored representation of the password."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password, stored):
    return hash_password(password) == stored
