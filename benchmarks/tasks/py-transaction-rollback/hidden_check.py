import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

Ledger = importlib.import_module("ledger").Ledger
ledger = Ledger()
before = dict(ledger.balances)
try:
    ledger.transfer("bob", "alice", 30)
except ValueError:
    pass
else:
    raise AssertionError("an overdrawn transfer must be rejected")
assert ledger.balances == before, "a rejected transfer changed account state"
