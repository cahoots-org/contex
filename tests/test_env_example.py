import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Set by the build or the rate limiter's own docs, not by operators via .env.
NOT_DOCUMENTED = {"CONTEX_VERSION", "RATE_LIMIT_DEFAULT", "RATE_LIMIT_WINDOW", "SENTRY_RELEASE"}


def test_env_example_matches_the_variables_the_code_reads():
    read = set()
    for path in [ROOT / "main.py", *(ROOT / "src").rglob("*.py")]:
        read |= set(re.findall(r'\w*(?:getenv|env|boost)\w*\(\s*"([A-Z][A-Z0-9_]+)"', path.read_text()))
    example = (ROOT / ".env.example").read_text()
    documented = set(re.findall(r"^# ([A-Z][A-Z0-9_]+)=", example, re.M))

    assert read - NOT_DOCUMENTED - documented == set()
    assert documented - read == set()
    assert not re.search(r"^[A-Z][A-Z0-9_]*=.*#", example, re.M)
