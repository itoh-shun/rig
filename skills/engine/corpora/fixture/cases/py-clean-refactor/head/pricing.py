"""Order pricing."""

from dataclasses import dataclass


@dataclass
class LineItem:
    sku: str
    unit_price_cents: int
    quantity: int


def line_total_cents(item: LineItem) -> int:
    return item.unit_price_cents * item.quantity


def _validate_discount(discount_percent: int) -> None:
    if discount_percent < 0:
        raise ValueError("discount_percent must not be negative")
    if discount_percent > 100:
        raise ValueError("discount_percent must not exceed 100")


def order_total_cents(items: list[LineItem], discount_percent: int) -> int:
    _validate_discount(discount_percent)
    subtotal = sum(line_total_cents(item) for item in items)
    discount = subtotal * discount_percent // 100
    return subtotal - discount
