# Publishing Contex Python SDK to PyPI

This guide covers how to publish the `contex-python` package to PyPI (Python Package Index).

## Prerequisites

1. **PyPI Account**: Create accounts on both:
   - [PyPI](https://pypi.org/account/register/) (production)
   - [TestPyPI](https://test.pypi.org/account/register/) (testing)

2. **API Tokens**: Generate API tokens for authentication:
   - Go to Account Settings → API tokens
   - Create a token with "Entire account" scope
   - Save the token securely (you'll only see it once!)

3. **Install Build Tools**:
   ```bash
   pip install build twine
   ```

## Publishing Process

### Step 1: Prepare the Package

```bash
cd sdk/python

# Clean previous builds
rm -rf dist/ build/ *.egg-info

# Update version in pyproject.toml if needed
# version = "0.2.0"  # Increment for new releases
```

### Step 2: Build the Package

```bash
# Build source distribution and wheel
python -m build

# This creates:
# - dist/contex-python-0.2.0.tar.gz (source)
# - dist/contex_python-0.2.0-py3-none-any.whl (wheel)
```

### Step 3: Test on TestPyPI (Recommended)

```bash
# Upload to TestPyPI first
python -m twine upload --repository testpypi dist/*

# You'll be prompted for:
# Username: __token__
# Password: <your TestPyPI token>

# Test installation from TestPyPI
pip install --index-url https://test.pypi.org/simple/ contex-python
```

### Step 4: Publish to PyPI

```bash
# Upload to production PyPI
python -m twine upload dist/*

# You'll be prompted for:
# Username: __token__
# Password: <your PyPI token>
```

### Step 5: Verify Installation

```bash
# Install from PyPI
pip install contex-python

# Test it works
python -c "from contex import ContexClient; print('✅ SDK installed successfully!')"
```

## Using API Tokens (Recommended)

Instead of entering credentials each time, configure them:

### Option 1: Environment Variables

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=<your-pypi-token>

# Now you can upload without prompts
python -m twine upload dist/*
```

### Option 2: `.pypirc` File

Create `~/.pypirc`:

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = <your-pypi-token>

[testpypi]
username = __token__
password = <your-testpypi-token>
repository = https://test.pypi.org/legacy/
```

**Important**: Add `.pypirc` to `.gitignore`!

Then upload with:
```bash
python -m twine upload --repository testpypi dist/*  # TestPyPI
python -m twine upload dist/*                         # PyPI
```

## Automated Publishing Script

Use the provided `publish.sh` script:

```bash
# Test release
./scripts/publish-sdk.sh test

# Production release
./scripts/publish-sdk.sh prod
```

## GitHub Actions (CI/CD)

For automated releases, add PyPI token to GitHub Secrets:
1. Go to repository Settings → Secrets and variables → Actions
2. Add secret: `PYPI_API_TOKEN`
3. Push a git tag to trigger release

```bash
git tag v0.2.0
git push origin v0.2.0
```

The GitHub Action will automatically build and publish.

## Version Management

### Semantic Versioning

Follow [SemVer](https://semver.org/):
- **MAJOR**: Breaking changes (1.0.0 → 2.0.0)
- **MINOR**: New features, backward compatible (0.1.0 → 0.2.0)
- **PATCH**: Bug fixes (0.2.0 → 0.2.1)

### Pre-release Versions

For beta/alpha releases:
```toml
# pyproject.toml
version = "0.2.0a1"  # Alpha
version = "0.2.0b1"  # Beta
version = "0.2.0rc1" # Release candidate
```

## Checklist Before Publishing

- [ ] All tests passing (`pytest`)
- [ ] Version number updated in `pyproject.toml`
- [ ] CHANGELOG.md updated
- [ ] README.md is current
- [ ] Examples work correctly
- [ ] Tested on TestPyPI
- [ ] Git tag created (`git tag v0.2.0`)
- [ ] Clean build directory

## Common Issues

### Issue: "File already exists"

**Problem**: Version already published to PyPI

**Solution**: Increment version number. PyPI doesn't allow re-uploading the same version.

### Issue: "Invalid credentials"

**Problem**: Wrong token or username

**Solution**: 
- Username must be `__token__` (literal string)
- Password is your API token (starts with `pypi-`)

### Issue: "Package name already taken"

**Problem**: `contex-python` name is taken

**Solution**: Choose a different name in `pyproject.toml`:
```toml
name = "contex-sdk"  # or "contex-client", etc.
```

### Issue: Import errors after installation

**Problem**: Package structure issues

**Solution**: Verify package structure:
```bash
python -m build
tar -tzf dist/contex-python-0.2.0.tar.gz | grep contex/
```

## Post-Publication

### 1. Verify on PyPI

Visit: https://pypi.org/project/contex-python/

Check:
- ✅ README renders correctly
- ✅ Version number is correct
- ✅ Links work
- ✅ Classifiers are appropriate

### 2. Test Installation

```bash
# Create fresh virtual environment
python -m venv test-env
source test-env/bin/activate

# Install from PyPI
pip install contex-python

# Run examples
python examples/basic_usage.py
```

### 3. Update Documentation

- Update main README.md with installation instructions
- Add to project documentation
- Announce on social media/blog

### 4. Create GitHub Release

```bash
# Tag the release
git tag -a v0.2.0 -m "Release v0.2.0"
git push origin v0.2.0

# Create release on GitHub
# Go to Releases → Draft a new release
# - Tag: v0.2.0
# - Title: "Contex Python SDK v0.2.0"
# - Description: Copy from CHANGELOG.md
```

## Continuous Deployment

For automatic PyPI publishing on git tags, see `.github/workflows/publish-sdk.yml`.

## Security Best Practices

1. **Never commit tokens**: Add to `.gitignore`
2. **Use scoped tokens**: Create project-specific tokens
3. **Rotate tokens**: Change tokens periodically
4. **Use 2FA**: Enable two-factor authentication on PyPI
5. **Sign releases**: Use GPG signing for releases

## Resources

- [Python Packaging Guide](https://packaging.python.org/)
- [PyPI Help](https://pypi.org/help/)
- [Twine Documentation](https://twine.readthedocs.io/)
- [Semantic Versioning](https://semver.org/)

## Quick Reference

```bash
# Complete publishing workflow
cd sdk/python
rm -rf dist/ build/ *.egg-info
python -m build
python -m twine check dist/*
python -m twine upload --repository testpypi dist/*  # Test first
python -m twine upload dist/*                         # Production
```

## Summary

Publishing to PyPI:
1. ✅ Build package: `python -m build`
2. ✅ Test on TestPyPI first
3. ✅ Upload to PyPI: `twine upload dist/*`
4. ✅ Verify installation works
5. ✅ Create GitHub release

Your SDK is now available to the world! 🎉
