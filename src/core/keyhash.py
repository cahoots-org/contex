import hashlib
import hmac
import os


def _get_salt() -> str | None:
    return os.getenv("API_KEY_SALT")


def hash_api_key(raw_key: str) -> str:
    salt = _get_salt()
    if salt:
        return hmac.new(salt.encode(), raw_key.encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(raw_key.encode()).hexdigest()


def candidate_hashes(raw_key: str) -> list[str]:
    salt = _get_salt()
    if salt:
        hm = hmac.new(salt.encode(), raw_key.encode(), hashlib.sha256).hexdigest()
        sha = hashlib.sha256(raw_key.encode()).hexdigest()
        return [hm, sha]
    return [hashlib.sha256(raw_key.encode()).hexdigest()]
