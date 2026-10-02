from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _format_amount(amount) -> str:
    if amount is None or amount == "":
        return "0"
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return str(amount)
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value.quantize(Decimal('0.01')):,.2f}"


@register.filter(name="commas")
def commas(amount) -> str:
    return _format_amount(amount)


@register.filter(name="money")
def money(amount, currency="KES") -> str:
    code = (currency or "KES").strip() or "KES"
    return f"{code} {_format_amount(amount)}"
