import subprocess


def ping(host, runner=subprocess.run):
    """Ping a host once and return the runner's result."""
    return runner("ping -c 1 " + host, shell=True)
