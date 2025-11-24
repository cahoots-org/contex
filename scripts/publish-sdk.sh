#!/bin/bash
# Publish Contex Python SDK to PyPI
# Usage: ./publish-sdk.sh [test|prod]

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
SDK_DIR="sdk/python"
PACKAGE_NAME="contex-python"

# Functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check arguments
if [ $# -eq 0 ]; then
    log_error "Usage: $0 [test|prod]"
    echo "  test - Publish to TestPyPI"
    echo "  prod - Publish to PyPI (production)"
    exit 1
fi

MODE=$1

# Validate mode
if [ "$MODE" != "test" ] && [ "$MODE" != "prod" ]; then
    log_error "Invalid mode: $MODE. Use 'test' or 'prod'"
    exit 1
fi

# Change to SDK directory
cd "$SDK_DIR" || exit 1

log_info "Publishing Contex Python SDK ($MODE mode)"

# Check if build tools are installed
if ! command -v python &> /dev/null; then
    log_error "Python not found. Please install Python 3.10+"
    exit 1
fi

if ! python -m pip show build &> /dev/null; then
    log_warn "Installing build tools..."
    python -m pip install build twine
fi

# Get version from pyproject.toml
VERSION=$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
log_info "Package version: $VERSION"

# Confirm production publish
if [ "$MODE" = "prod" ]; then
    log_warn "You are about to publish to PRODUCTION PyPI!"
    log_warn "Version: $VERSION"
    read -p "Are you sure? (yes/no): " -r
    if [[ ! $REPLY =~ ^yes$ ]]; then
        log_info "Publish cancelled"
        exit 0
    fi
fi

# Clean previous builds
log_info "Cleaning previous builds..."
rm -rf dist/ build/ *.egg-info contex.egg-info

# Run tests (if they exist)
if [ -f "tests/test_client.py" ]; then
    log_info "Running tests..."
    python -m pytest tests/ || {
        log_error "Tests failed! Fix tests before publishing."
        exit 1
    }
fi

# Build package
log_info "Building package..."
python -m build

# Check distribution
log_info "Checking distribution..."
python -m twine check dist/*

# Upload
if [ "$MODE" = "test" ]; then
    log_info "Uploading to TestPyPI..."
    python -m twine upload --repository testpypi dist/*
    
    log_info "✅ Published to TestPyPI!"
    log_info "Test installation with:"
    echo "  pip install --index-url https://test.pypi.org/simple/ $PACKAGE_NAME"
    echo "  View at: https://test.pypi.org/project/$PACKAGE_NAME/"
else
    log_info "Uploading to PyPI..."
    python -m twine upload dist/*
    
    log_info "✅ Published to PyPI!"
    log_info "Install with:"
    echo "  pip install $PACKAGE_NAME"
    echo "  View at: https://pypi.org/project/$PACKAGE_NAME/"
    
    # Suggest creating git tag
    log_info "Don't forget to create a git tag:"
    echo "  git tag v$VERSION"
    echo "  git push origin v$VERSION"
fi

log_info "Publishing complete! 🎉"
