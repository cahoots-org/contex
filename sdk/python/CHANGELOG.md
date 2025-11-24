# Changelog

All notable changes to the Contex Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2024-11-24

### Added
- Initial release of Contex Python SDK
- Async client (`ContexAsyncClient`) with full API support
- Sync client (`ContexClient`) wrapper for simple scripts
- Complete API coverage:
  - Data publishing
  - Agent registration and management
  - Querying with semantic search
  - API key management
  - Health checks and status
- Comprehensive error handling with custom exceptions
- Retry logic with exponential backoff
- Type hints with Pydantic models
- Context manager support for resource cleanup
- Rate limit handling
- Full documentation and examples

### Features
- **Async/Sync Support**: Both async and synchronous interfaces
- **Type Safety**: Full type annotations with Pydantic
- **Error Handling**: Comprehensive exception hierarchy
- **Retry Logic**: Automatic retries with exponential backoff
- **Rate Limiting**: Built-in rate limit detection and handling
- **Documentation**: Complete API docs and usage examples

### Examples
- `basic_usage.py` - Basic SDK usage
- `error_handling.py` - Error handling patterns

## [Unreleased]

### Planned
- WebSocket support for real-time updates
- Batch operations API
- Advanced retry strategies
- Request/response logging
- Metrics collection
- CLI tool integration

---

## Version History

- **0.2.0** (2024-11-24) - Initial release

[0.2.0]: https://github.com/cahoots-org/contex/releases/tag/v0.2.0
