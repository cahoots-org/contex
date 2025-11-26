# Golden Integration Tests

## Overview

Golden integration tests are comprehensive end-to-end tests designed to:
- **Be deterministic** - same inputs always produce same outputs
- **Be git bisect compatible** - reliable for finding breaking commits
- **Test critical workflows** - cover all essential system functionality
- **Run in CI/CD** - automated testing before releases

## Running Tests

### Quick Run (Unit-style, FakeRedis)
```bash
# Run simplified golden tests with mocked dependencies
pytest tests/test_golden_unit.py -v
```

### Full Integration Run (Requires Docker)
```bash
# Start infrastructure
docker compose up -d redis opensearch

# Run comprehensive golden tests
pytest tests/test_golden_integration.py -v

# Cleanup
docker compose down
```

## Test Categories

###  Critical Workflows
- ✅ Publish → Query workflow
- ✅ Agent registration → Notification workflow
- ✅ Hybrid search (semantic + keyword)
- ✅ CSV/JSON/YAML/XML data format support

### Security
- ✅ API key authentication
- ✅ RBAC project access control
- ✅ Rate limiting enforcement

### Data Management
- ✅ Data retention policies
- ✅ Export/import functionality
- ✅ Multi-format data normalization

### System Health
- ✅ Health check endpoints
- ✅ Metrics collection

## CI/CD Usage

Add to your GitHub Actions workflow:

```yaml
- name: Run Golden Integration Tests
  run: |
    docker compose up -d redis opensearch
    pytest tests/test_golden_integration.py -v --maxfail=1
    docker compose down
```

## Git Bisect Usage

Find the commit that broke a specific workflow:

```bash
# Start bisecting
git bisect start
git bisect bad  # Current broken version
git bisect good v0.1.1  # Last known good version

# Run golden tests automatically
git bisect run pytest tests/test_golden_integration.py::TestGoldenPublishQueryWorkflow -v
```

## Adding New Golden Tests

When adding critical features:

1. **Write the test first** - test-driven development
2. **Make it deterministic** - no random data, fixed timestamps
3. **Test the happy path** - focus on successful workflows
4. **Clean up after** - ensure tests don't interfere with each other
5. **Document the workflow** - clear test names and docstrings

Example:

```python
@pytest.mark.asyncio
async def test_new_critical_workflow(test_client, test_redis):
    """Golden test: Brief description of workflow"""

    # 1. Setup - create necessary data
    # 2. Execute - perform the workflow
    # 3. Assert - verify expected outcomes
    # 4. Cleanup - handled by fixtures
```
