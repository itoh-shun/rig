import os


def read_note(base_dir, name):
    # Reject the obvious ``..`` traversal sequence.
    if ".." in name:
        raise ValueError("invalid name")
    path = os.path.join(base_dir, name)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()
