import hashlib


def sha256_hash(data: str) -> str:
    """Compute SHA-256 hash of string data."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def fingerprint_token(bearer_token: str) -> str:
    """Generate SHA-256 fingerprint of bearer token for secure storage."""
    return sha256_hash(bearer_token)
