import secrets


def new_reset_token():
    return secrets.token_hex(16)
