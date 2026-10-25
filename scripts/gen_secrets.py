"""
scripts/gen_secrets.py — Print a fresh SECRET_KEY and CRON_SECRET.
Usage: python scripts/gen_secrets.py
"""
import secrets
import string


def gen_django_key(length: int = 50) -> str:
    """Generate a Django-compatible SECRET_KEY."""
    alphabet = string.ascii_letters + string.digits + '!@#$%^&*(-_=+)'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def gen_hex_token(bytes_: int = 32) -> str:
    """Generate a secure hex token (e.g. for CRON_SECRET)."""
    return secrets.token_hex(bytes_)


if __name__ == '__main__':
    print("# Copy these into your .env file")
    print(f"SECRET_KEY={gen_django_key()}")
    print(f"CRON_SECRET={gen_hex_token()}")
