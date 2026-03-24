import re


def format_phone_number(phone):
    phone = phone.strip().replace(" ", "")

    if phone.startswith("+254"):
        normalized = phone
    elif phone.startswith("254"):
        normalized = f"+{phone}"
    elif phone.startswith("0"):
        normalized = "+254" + phone[1:]
    elif phone.startswith("7"):
        normalized = "+254" + phone
    else:
        return None

    # Validate (Kenyan numbers start with 7 and have 9 digits after 254)
    if re.fullmatch(r"\+2547\d{8}", normalized):
        return normalized

    return None
