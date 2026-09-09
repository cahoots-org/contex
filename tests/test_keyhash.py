import hashlib
import hmac
import pytest
from src.core import keyhash


def test_no_pepper_is_plain_sha(monkeypatch):
    monkeypatch.setattr(keyhash, "_get_pepper", lambda: None)
    raw = "ck_abc"
    assert keyhash.hash_api_key(raw) == hashlib.sha256(raw.encode()).hexdigest()
    assert keyhash.candidate_hashes(raw) == [hashlib.sha256(raw.encode()).hexdigest()]


def test_pepper_uses_hmac_and_dual_verify(monkeypatch):
    monkeypatch.setattr(keyhash, "_get_pepper", lambda: "pepper")
    raw = "ck_abc"
    hm = hmac.new(b"pepper", raw.encode(), hashlib.sha256).hexdigest()
    sha = hashlib.sha256(raw.encode()).hexdigest()
    assert keyhash.hash_api_key(raw) == hm
    assert keyhash.candidate_hashes(raw) == [hm, sha]
