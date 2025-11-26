#!/usr/bin/env python3
"""
Contex Performance Benchmark

Measures actual latency for:
- Embedding generation (cold vs cached)
- Vector search operations
- Full semantic matching pipeline
- Comparison across different data sizes
"""

import asyncio
import time
import statistics
import json
from typing import List, Dict, Any
import sys

from redis.asyncio import Redis
from src.core.semantic_matcher import SemanticDataMatcher
from src.core.embedding_cache import EmbeddingCache


class BenchmarkResults:
    """Store and display benchmark results"""

    def __init__(self):
        self.results: Dict[str, List[float]] = {}

    def add(self, name: str, latency_ms: float):
        """Add a measurement"""
        if name not in self.results:
            self.results[name] = []
        self.results[name].append(latency_ms)

    def print_summary(self):
        """Print benchmark summary"""
        print("\n" + "=" * 70)
        print("BENCHMARK RESULTS")
        print("=" * 70)

        for name, measurements in sorted(self.results.items()):
            if not measurements:
                continue

            avg = statistics.mean(measurements)
            median = statistics.median(measurements)
            p95 = sorted(measurements)[int(len(measurements) * 0.95)] if len(measurements) > 1 else measurements[0]
            min_val = min(measurements)
            max_val = max(measurements)

            print(f"\n{name}:")
            print(f"  Samples:  {len(measurements)}")
            print(f"  Average:  {avg:.2f}ms")
            print(f"  Median:   {median:.2f}ms")
            print(f"  P95:      {p95:.2f}ms")
            print(f"  Min:      {min_val:.2f}ms")
            print(f"  Max:      {max_val:.2f}ms")

        print("\n" + "=" * 70)


async def benchmark_embedding_generation(matcher: SemanticDataMatcher, results: BenchmarkResults):
    """Benchmark raw embedding generation"""
    print("\n📊 Benchmarking embedding generation...")

    test_texts = [
        "What is the authentication configuration?",
        "Show me the database schema for users",
        "How do I configure Redis connection?",
        "What are the API endpoints available?",
        "Explain the rate limiting policy",
    ]

    # Cold start (no cache)
    for text in test_texts:
        start = time.perf_counter()
        embedding = matcher.model.encode(text)
        latency = (time.perf_counter() - start) * 1000
        results.add("1. Embedding Generation (COLD)", latency)
        print(f"  ⏱️  Cold: {latency:.2f}ms - '{text[:40]}...'")

    # Warm (with cache)
    await matcher.embedding_cache.clear()

    for text in test_texts:
        # Prime cache
        embedding = matcher.model.encode(text)
        await matcher.embedding_cache.set(text, embedding)

    for text in test_texts:
        start = time.perf_counter()
        cached_embedding = await matcher.embedding_cache.get(text)
        latency = (time.perf_counter() - start) * 1000
        results.add("2. Embedding Lookup (CACHED)", latency)
        print(f"  ⚡ Cached: {latency:.2f}ms - '{text[:40]}...'")


async def benchmark_vector_search(matcher: SemanticDataMatcher, redis: Redis, results: BenchmarkResults):
    """Benchmark vector search with different data sizes"""
    print("\n📊 Benchmarking vector search...")

    project_id = "benchmark_project"

    # Test with different dataset sizes
    for size in [10, 50, 100, 500, 1000]:
        print(f"\n  Dataset size: {size} documents")

        # Clear previous data
        await matcher.clear_project(project_id)

        # Create test dataset
        for i in range(size):
            data = {
                "id": i,
                "name": f"Document {i}",
                "description": f"This is test document number {i} with some content",
                "category": ["api", "config", "schema", "auth"][i % 4],
                "value": i * 100
            }

            await matcher.register_data(
                project_id=project_id,
                data_key=f"doc_{i}",
                data=data
            )

        # Give index time to update
        await asyncio.sleep(0.5)

        # Test queries
        test_queries = [
            "authentication configuration",
            "API endpoints",
            "database schema"
        ]

        for query in test_queries:
            start = time.perf_counter()
            result = await matcher.match_agent_needs(project_id, [query])
            matches = result.get(query, [])
            latency = (time.perf_counter() - start) * 1000
            results.add(f"3. Vector Search ({size} docs)", latency)
            print(f"    ⏱️  {latency:.2f}ms - '{query}' -> {len(matches)} matches")


async def benchmark_full_pipeline(matcher: SemanticDataMatcher, redis: Redis, results: BenchmarkResults):
    """Benchmark complete semantic matching pipeline"""
    print("\n📊 Benchmarking full pipeline (end-to-end)...")

    project_id = "benchmark_pipeline"
    await matcher.clear_project(project_id)

    # Setup realistic dataset (100 docs)
    sample_data = [
        {"key": "auth_config", "desc": "Authentication configuration using JWT tokens", "data": {"method": "jwt", "expiry": 3600}},
        {"key": "db_users", "desc": "Database schema for users table", "data": {"table": "users", "fields": ["id", "email", "password"]}},
        {"key": "redis_config", "desc": "Redis connection configuration", "data": {"host": "localhost", "port": 6379}},
        {"key": "api_health", "desc": "Health check API endpoint", "data": {"path": "/health", "method": "GET"}},
        {"key": "rate_limit", "desc": "Rate limiting policy for API requests", "data": {"limit": 100, "window": 60}},
    ]

    # Populate with variations
    for i in range(20):
        for item in sample_data:
            await matcher.register_data(
                project_id=project_id,
                data_key=f"{item['key']}_{i}",
                data=item["data"]
            )

    await asyncio.sleep(0.5)

    # Test queries (cold - no embeddings cached)
    test_queries = [
        "How do I authenticate users?",
        "What is the database schema?",
        "Show me Redis configuration",
        "API endpoint for health check",
        "What are the rate limits?",
    ]

    print("\n  Cold queries (no cache):")
    await matcher.embedding_cache.clear()

    for query in test_queries:
        start = time.perf_counter()
        result = await matcher.match_agent_needs(project_id, [query])
        matches = result.get(query, [])
        latency = (time.perf_counter() - start) * 1000
        results.add("4. Full Pipeline (COLD)", latency)
        print(f"    ❄️  {latency:.2f}ms - '{query}' -> {len(matches)} matches")

    # Test queries (warm - embeddings cached)
    print("\n  Warm queries (cached):")

    for query in test_queries:
        start = time.perf_counter()
        result = await matcher.match_agent_needs(project_id, [query])
        matches = result.get(query, [])
        latency = (time.perf_counter() - start) * 1000
        results.add("5. Full Pipeline (CACHED)", latency)
        print(f"    🔥 {latency:.2f}ms - '{query}' -> {len(matches)} matches")


async def benchmark_manual_lookup(redis: Redis, results: BenchmarkResults):
    """Benchmark manual key-based lookup for comparison"""
    print("\n📊 Benchmarking manual lookup (baseline)...")

    project_id = "benchmark_manual"

    # Store data with known keys
    for i in range(100):
        await redis.hset(
            f"data:{project_id}:doc_{i}",
            mapping={
                "data": json.dumps({"id": i, "value": i * 100}),
                "description": f"Document {i}"
            }
        )

    # Lookup by exact key
    for i in range(20):
        key = f"data:{project_id}:doc_{i * 5}"
        start = time.perf_counter()
        data = await redis.hgetall(key)
        latency = (time.perf_counter() - start) * 1000
        results.add("6. Manual Lookup (BASELINE)", latency)
        print(f"  ⚡ {latency:.2f}ms - Direct Redis lookup")


async def main():
    """Run all benchmarks"""
    print("=" * 70)
    print("CONTEX PERFORMANCE BENCHMARK")
    print("=" * 70)
    print("\nInitializing...")

    # Connect to Redis
    redis = Redis.from_url("redis://localhost:6379", decode_responses=False)

    try:
        await redis.ping()
        print("✅ Redis connected")
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        print("Please start Redis: docker-compose up -d redis")
        sys.exit(1)

    # Initialize semantic matcher
    matcher = SemanticDataMatcher(redis=redis)
    await matcher.initialize_index()
    print("✅ Semantic matcher initialized")

    results = BenchmarkResults()

    try:
        # Run benchmarks
        await benchmark_embedding_generation(matcher, results)
        await benchmark_vector_search(matcher, redis, results)
        await benchmark_full_pipeline(matcher, redis, results)
        await benchmark_manual_lookup(redis, results)

        # Print summary
        results.print_summary()

        # Print analysis
        print("\n📈 ANALYSIS")
        print("=" * 70)

        cold_avg = statistics.mean(results.results.get("4. Full Pipeline (COLD)", [0]))
        warm_avg = statistics.mean(results.results.get("5. Full Pipeline (CACHED)", [0]))
        manual_avg = statistics.mean(results.results.get("6. Manual Lookup (BASELINE)", [0]))

        overhead = cold_avg - manual_avg
        speedup = cold_avg / warm_avg if warm_avg > 0 else 1

        print(f"\nSemantic matching adds ~{overhead:.1f}ms overhead vs manual lookup")
        print(f"Caching provides {speedup:.1f}x speedup for repeated queries")
        print(f"\n✅ Cold query: ~{cold_avg:.1f}ms")
        print(f"✅ Warm query: ~{warm_avg:.1f}ms")
        print(f"✅ Manual lookup (baseline): ~{manual_avg:.1f}ms")

        print("\n" + "=" * 70)

    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
