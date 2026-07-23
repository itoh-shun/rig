import importlib
import pathlib
import random
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

new_reset_token = importlib.import_module("reset").new_reset_token

random.seed(1337)
first = new_reset_token()
random.seed(1337)
second = new_reset_token()

# A CSPRNG cannot be reproduced by re-seeding the standard PRNG; a Mersenne
# Twister token can, which is exactly the predictability an attacker exploits.
assert first != second, "reset token is drawn from a seedable PRNG and is predictable"
