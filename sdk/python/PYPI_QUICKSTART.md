# PyPI Publishing Quick Reference

## One-Time Setup

### 1. Create PyPI Accounts
- Production: https://pypi.org/account/register/
- Testing: https://test.pypi.org/account/register/

### 2. Generate API Tokens
1. Log in to PyPI
2. Go to Account Settings → API tokens
3. Click "Add API token"
4. Scope: "Entire account" (or project-specific)
5. Copy token (starts with `pypi-...`)

### 3. Install Tools
```bash
pip install build twine
```

### 4. Configure Credentials (Optional)
Create `~/.pypirc`:
```ini
[pypi]
username = __token__
password = pypi-YOUR-TOKEN-HERE

[testpypi]
username = __token__
password = pypi-YOUR-TESTPYPI-TOKEN-HERE
repository = https://test.pypi.org/legacy/
```

**⚠️ IMPORTANT**: Never commit this file!

---

## Publishing Workflow

### Manual Publishing

```bash
# 1. Navigate to SDK directory
cd sdk/python

# 2. Update version in pyproject.toml
# version = "0.2.1"

# 3. Clean and build
rm -rf dist/ build/ *.egg-info
python -m build

# 4. Check package
python -m twine check dist/*

# 5. Test on TestPyPI first (recommended)
python -m twine upload --repository testpypi dist/*

# 6. Test installation
pip install --index-url https://test.pypi.org/simple/ contex-python

# 7. If all good, publish to production PyPI
python -m twine upload dist/*

# 8. Create git tag
git tag v0.2.1
git push origin v0.2.1
```

### Using the Script

```bash
# Test release
./scripts/publish-sdk.sh test

# Production release
./scripts/publish-sdk.sh prod
```

### Automated (GitHub Actions)

```bash
# 1. Add PyPI token to GitHub Secrets
# Settings → Secrets → Actions → New secret
# Name: PYPI_API_TOKEN
# Value: pypi-...

# 2. Update version in pyproject.toml
# version = "0.2.1"

# 3. Commit and tag
git add sdk/python/pyproject.toml
git commit -m "Bump SDK version to 0.2.1"
git tag v0.2.1
git push origin main
git push origin v0.2.1

# GitHub Actions will automatically publish!
```

---

## Common Commands

```bash
# Build package
python -m build

# Check package
python -m twine check dist/*

# Upload to TestPyPI
python -m twine upload --repository testpypi dist/*

# Upload to PyPI
python -m twine upload dist/*

# Install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ contex-python

# Install from PyPI
pip install contex-python

# Install specific version
pip install contex-python==0.2.0
```

---

## Troubleshooting

### "File already exists"
- **Cause**: Version already published
- **Fix**: Increment version number in `pyproject.toml`

### "Invalid credentials"
- **Cause**: Wrong token or username
- **Fix**: Username must be `__token__`, password is your API token

### "403 Forbidden"
- **Cause**: Insufficient permissions
- **Fix**: Use account-scoped token or project-specific token

### Package not found after publishing
- **Wait**: Can take a few minutes to appear
- **Check**: https://pypi.org/project/contex-python/

---

## Version Numbering

Follow Semantic Versioning (SemVer):
- **MAJOR.MINOR.PATCH** (e.g., 1.2.3)
- **MAJOR**: Breaking changes (1.0.0 → 2.0.0)
- **MINOR**: New features, backward compatible (0.1.0 → 0.2.0)
- **PATCH**: Bug fixes (0.2.0 → 0.2.1)

Pre-releases:
- `0.2.0a1` - Alpha
- `0.2.0b1` - Beta
- `0.2.0rc1` - Release candidate

---

## Checklist

Before publishing:
- [ ] All tests passing
- [ ] Version updated in `pyproject.toml`
- [ ] `CHANGELOG.md` updated
- [ ] Tested on TestPyPI
- [ ] README renders correctly
- [ ] Examples work
- [ ] Git committed and tagged

---

## Links

- **PyPI**: https://pypi.org/project/contex-python/
- **TestPyPI**: https://test.pypi.org/project/contex-python/
- **Docs**: https://packaging.python.org/
- **Twine**: https://twine.readthedocs.io/
