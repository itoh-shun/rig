import subprocess

_BLOCKED = [";", "&", "|", "`", "\n"]


def ping(host, runner=subprocess.run):
    for token in _BLOCKED:
        if token in host:
            raise ValueError("invalid host")
    return runner("ping -c 1 " + host, shell=True)
