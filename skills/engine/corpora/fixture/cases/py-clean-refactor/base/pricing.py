"""Order pricing."""

from dataclasses import dataclass


@dataclass
class LineItem:
    sku: str
    unit_price_cents: int
    quantity: int


def line_total_cents(item: LineItem) -> int:
    return item.unit_price_cents * item.quantity


def order_total_cents(items: list[LineItem], discount_percent: int) -> int:
    subtotal = 0
    for item in items:
        subtotal = subtotal + item.unit_price_cents * item.quantity
    if discount_percent < 0:
        raise ValueError("discount_percent must not be negative")
    if discount_percent > 100:
        raise ValueError("discount_percent must not exceed 100")
    discount = subtotal * discount_percent // 100
    return subtotal - discount
