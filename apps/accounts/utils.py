def mask_email(email):
    """"durga123@gmail.com" -> "d******3@gmail.com". Used anywhere a DGC or
    superadmin sees another user's email without needing the real address."""
    local, _, domain = (email or "").partition("@")
    if not domain:
        return email or ""
    if len(local) <= 2:
        masked = local[:1] + "*" * max(len(local) - 1, 1)
    else:
        masked = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked}@{domain}"
