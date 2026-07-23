import subprocess


def ping(host, runner=subprocess.run):
    return runner(["ping", "-c", "1", host], shell=False)
