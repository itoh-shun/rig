import random


def new_reset_token():
    """Return a fresh password-reset token."""
    return "".join(random.choice("0123456789abcdef") for _ in range(32))
