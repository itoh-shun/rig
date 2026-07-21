class Ledger:
    def __init__(self):
        self.balances = {"alice": 100, "bob": 20}

    def transfer(self, source, destination, amount):
        if amount <= 0:
            raise ValueError("amount must be positive")
        if amount > self.balances[source]:
            raise ValueError("insufficient funds")
        self.balances[source] -= amount
        self.balances[destination] += amount
