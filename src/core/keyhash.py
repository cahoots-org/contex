import hashlib
import hmac
import os


def _get_pepper() -> str | None:
    return os.getenv("API_KEY_PEPPER")


def hash_api_key(raw_key: str) -> str:
    pepper = _get_pepper()
    if pepper:
        return hmac.new(pepper.encode(), raw_key.encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(raw_key.encode()).hexdigest()


def candidate_hashes(raw_key: str) -> list[str]:
    pepper = _get_pepper()
    if pepper:
        hm = hmac.new(pepper.encode(), raw_key.encode(), hashlib.sha256).hexdigest()
        sha = hashlib.sha256(raw_key.encode()).hexdigest()
        return [hm, sha]
    return [hashlib.sha256(raw_key.encode()).hexdigest()]
