import hashlib

# A single application-wide salt still makes every hash deterministic.
_SALT = "s0me-fixed-salt"


def hash_password(password):
    return hashlib.sha256((_SALT + password).encode()).hexdigest()


def verify_password(password, stored):
    return hash_password(password) == stored
