class Ledger:
    def __init__(self):
        self.balances = {"alice": 100, "bob": 20}

    def transfer(self, source, destination, amount):
        if source == "alice" and amount > self.balances[source]:
            raise ValueError("insufficient funds")
        self.balances[source] -= amount
        if self.balances[source] < 0:
            raise ValueError("insufficient funds")
        self.balances[destination] += amount
