# Contributing to Contex

Thank you for your interest in contributing to Contex! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Coding Guidelines](#coding-guidelines)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Features](#suggesting-features)

## Code of Conduct

By participating in this project, you agree to maintain a respectful and inclusive environment. We expect all contributors to:

- Be respectful and considerate in communication
- Welcome newcomers and help them get started
- Accept constructive criticism gracefully
- Focus on what's best for the community and project
- Show empathy towards other community members

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/contex.git
   cd contex
   ```
3. **Add the upstream remote**:
   ```bash
   git remote add upstream https://github.com/contex/contex.git
   ```

## Development Setup

### Prerequisites

- Python 3.11+
- Redis 7.0+
- Git

### Local Setup

1. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Start Redis** (if not using Docker):
   ```bash
   # macOS
   brew services start redis

   # Linux
   sudo systemctl start redis

   # Docker
   docker run -d -p 6379:6379 redis:7-alpine
   ```

4. **Run Contex**:
   ```bash
   python main.py
   ```

5. **Run tests**:
   ```bash
   pytest tests/ -v
   ```

### Docker Setup

Alternatively, use Docker Compose:

```bash
docker compose up -d
docker compose logs -f contex
```

## How to Contribute

### Types of Contributions

We welcome all types of contributions:

- **Bug fixes**: Fix issues in the codebase
- **Features**: Add new functionality
- **Documentation**: Improve or add documentation
- **Tests**: Add or improve test coverage
- **Examples**: Create example use cases
- **Performance**: Optimize code performance
- **Refactoring**: Improve code quality

### Contribution Workflow

1. **Check existing issues** to see if your contribution is already being discussed
2. **Create an issue** if one doesn't exist (for bugs or features)
3. **Discuss your approach** in the issue before starting major work
4. **Create a feature branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
5. **Make your changes** following our coding guidelines
6. **Write tests** for your changes
7. **Run tests** to ensure everything passes
8. **Commit your changes** with clear commit messages
9. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```
10. **Open a Pull Request** against the `main` branch

## Coding Guidelines

### Python Style

We follow [PEP 8](https://pep8.org/) style guidelines with some modifications:

- **Line length**: 100 characters (not 79)
- **Quotes**: Double quotes for strings
- **Type hints**: Use type hints for function signatures
- **Docstrings**: Use Google-style docstrings

Example:

```python
from typing import Dict, List, Any

def process_data(
    project_id: str,
    data: Dict[str, Any],
    max_results: int = 10
) -> List[Dict[str, Any]]:
    """
    Process data for a project.

    Args:
        project_id: Unique project identifier
        data: Data to process
        max_results: Maximum number of results to return

    Returns:
        List of processed data dictionaries

    Raises:
        ValueError: If project_id is invalid
    """
    # Implementation here
    pass
```

### Code Organization

- **Imports**: Group stdlib, third-party, and local imports
- **Constants**: Use UPPER_CASE for constants
- **Classes**: Use PascalCase
- **Functions**: Use snake_case
- **Private methods**: Prefix with underscore (`_method_name`)

### Error Handling

- Use specific exception types
- Provide helpful error messages
- Log errors appropriately

```python
try:
    result = await risky_operation()
except SpecificError as e:
    logger.error(f"Operation failed: {e}")
    raise HTTPException(status_code=500, detail="Clear error message")
```

## Testing

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_context_engine.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# View coverage report
open htmlcov/index.html
```

### Writing Tests

- **Test file naming**: `test_<module_name>.py`
- **Test function naming**: `test_<what_is_tested>()`
- **Use fixtures**: For setup and teardown
- **Test edge cases**: Not just happy paths
- **Keep tests focused**: One concept per test

Example:

```python
import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from src.context_engine import ContextEngine

@pytest_asyncio.fixture
async def redis():
    """Create a fake Redis instance for testing"""
    return FakeAsyncRedis(decode_responses=False)

@pytest_asyncio.fixture
async def context_engine(redis):
    """Create a ContextEngine instance for testing"""
    return ContextEngine(redis=redis)

@pytest.mark.asyncio
async def test_publish_data_creates_event(context_engine):
    """Test that publishing data creates an event in the store"""
    # Arrange
    event = DataPublishEvent(
        project_id="test-project",
        data_key="test-key",
        data={"value": 123}
    )

    # Act
    sequence = await context_engine.publish_data(event)

    # Assert
    assert sequence is not None
    assert int(sequence) > 0
```

### Test Coverage

- Maintain **at least 90% code coverage**
- Cover edge cases and error conditions
- Test async code properly with `@pytest.mark.asyncio`

## Submitting Changes

### Commit Messages

Use clear, descriptive commit messages following this format:

```
<type>: <short summary>

<detailed description (optional)>

<issue reference (if applicable)>
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `test`: Adding or updating tests
- `refactor`: Code refactoring
- `perf`: Performance improvements
- `chore`: Maintenance tasks

Examples:

```
feat: add webhook retry mechanism with exponential backoff

Implements retry logic for webhook deliveries with configurable
max retries and exponential backoff delay.

Fixes #123
```

```
fix: correct semantic matching threshold comparison

The similarity threshold was being compared incorrectly,
causing some valid matches to be excluded.
```

### Pull Request Guidelines

1. **Fill out the PR template** completely
2. **Link related issues** using "Fixes #123" or "Relates to #456"
3. **Keep PRs focused**: One feature or fix per PR
4. **Update documentation** if adding features
5. **Add tests** for new functionality
6. **Ensure tests pass**: CI must be green
7. **Request reviews** from maintainers

### PR Checklist

Before submitting, ensure:

- [ ] Code follows style guidelines
- [ ] All tests pass locally
- [ ] New tests added for new features
- [ ] Documentation updated (if needed)
- [ ] Commit messages are clear
- [ ] No merge conflicts with main branch
- [ ] Code coverage maintained or improved

## Reporting Bugs

### Before Reporting

1. **Search existing issues** to avoid duplicates
2. **Try the latest version** to see if it's already fixed
3. **Gather information** about your environment

### Bug Report Template

When creating a bug report, include:

```markdown
**Description**
A clear description of the bug.

**Steps to Reproduce**
1. Step one
2. Step two
3. See error

**Expected Behavior**
What you expected to happen.

**Actual Behavior**
What actually happened.

**Environment**
- Contex version: X.Y.Z
- Python version: 3.11.x
- Redis version: 7.x
- OS: Ubuntu 22.04 / macOS 13.x / Windows 11
- Docker version (if applicable): XX.XX.X

**Logs**
```
Paste relevant logs here
```

**Additional Context**
Any other relevant information.
```

## Suggesting Features

### Feature Request Template

When suggesting a feature:

```markdown
**Problem Statement**
Describe the problem this feature would solve.

**Proposed Solution**
Describe your proposed solution.

**Alternatives Considered**
What alternatives have you considered?

**Use Cases**
Describe concrete use cases for this feature.

**Additional Context**
Any other relevant information, mockups, or examples.
```

### Feature Discussion

- Discuss in an issue before implementing
- Consider if it fits the project's goals
- Think about backwards compatibility
- Consider the maintenance burden

## Development Tips

### Useful Commands

```bash
# Format code with black
black src/ tests/

# Check types with mypy
mypy src/

# Lint code
flake8 src/ tests/

# Run specific test
pytest tests/test_context_engine.py::test_specific_function -v

# Debug with pdb
pytest tests/ --pdb  # Drop into debugger on failure
```

### Debugging

- Use `print()` statements or `logging` for simple debugging
- Use Python debugger (`pdb`) for complex issues:
  ```python
  import pdb; pdb.set_trace()
  ```
- Check logs with `docker compose logs -f contex`

### Working with Redis

```bash
# Connect to Redis CLI
redis-cli

# Or with Docker
docker exec -it contex-redis redis-cli

# View all keys
KEYS *

# View stream data
XREAD STREAMS project:test-project:events 0
```

## Questions?

- **GitHub Issues**: For bug reports and feature requests
- **Discussions**: For questions and general discussion
- **Discord**: Join our community (link in README)

## License

By contributing to Contex, you agree that your contributions will be licensed under the MIT License.

---

**Thank you for contributing to Contex!** 🎉
