from connectors.base import allowed, matches_any


def test_matches_any():
    assert matches_any("public.users", ["public.*"])
    assert not matches_any("audit.log", ["public.*"])
    assert not matches_any("anything", None)
    assert not matches_any("anything", [])


def test_no_lists_allows_everything():
    assert allowed("public.users")


def test_include_keeps_only_matches():
    assert allowed("public.users", include=["public.*"])
    assert not allowed("audit.trail", include=["public.*"])


def test_exclude_rejects_matches():
    assert allowed("public.users", exclude=["public.sessions"])
    assert not allowed("public.sessions", exclude=["public.sessions"])


def test_exclude_wins_over_include():
    assert not allowed(
        "public.sessions",
        include=["public.*"],
        exclude=["*.sessions"],
    )
