"""Tests for Data Versioning"""

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis

from src.core.versioning import (
    VersionManager,
    DataVersion,
    VersionDiff,
    VersionHistory,
    init_version_manager,
    get_version_manager,
)


class TestVersionModels:
    """Test version model validation"""

    def test_data_version_model(self):
        """Test DataVersion model"""
        version = DataVersion(
            version=1,
            data_key="test_key",
            project_id="proj1",
            data={"key": "value"},
            data_hash="abc123",
            data_format="json",
            created_at="2024-01-01T00:00:00Z",
        )
        assert version.version == 1
        assert version.data_key == "test_key"
        assert version.change_type == "create"

    def test_version_diff_model(self):
        """Test VersionDiff model"""
        diff = VersionDiff(
            from_version=1,
            to_version=2,
            data_key="test_key",
            added_keys=["new_key"],
            removed_keys=["old_key"],
            modified_keys=["changed_key"],
            unchanged_keys=["stable_key"],
            changes={
                "new_key": {"from": None, "to": "new_value"},
                "old_key": {"from": "old_value", "to": None},
            }
        )
        assert diff.from_version == 1
        assert len(diff.added_keys) == 1

    def test_version_history_model(self):
        """Test VersionHistory model"""
        history = VersionHistory(
            data_key="test_key",
            project_id="proj1",
            total_versions=5,
            current_version=5,
            versions=[],
        )
        assert history.total_versions == 5


class TestVersionManager:
    """Test VersionManager functionality"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    @pytest_asyncio.fixture
    async def manager(self, redis):
        """Create a version manager"""
        return VersionManager(redis, max_versions=10)

    @pytest.mark.asyncio
    async def test_create_first_version(self, manager):
        """Test creating the first version of data"""
        version = await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"name": "test", "value": 123},
            description="Initial version",
            created_by="user1",
        )

        assert version.version == 1
        assert version.data_key == "test_key"
        assert version.project_id == "proj1"
        assert version.data == {"name": "test", "value": 123}
        assert version.change_type == "create"
        assert version.previous_version is None

    @pytest.mark.asyncio
    async def test_create_subsequent_versions(self, manager):
        """Test creating multiple versions"""
        # Create first version
        v1 = await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"value": 1},
        )
        assert v1.version == 1

        # Create second version
        v2 = await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"value": 2},
        )
        assert v2.version == 2
        assert v2.change_type == "update"
        assert v2.previous_version == 1

        # Create third version
        v3 = await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"value": 3},
        )
        assert v3.version == 3

    @pytest.mark.asyncio
    async def test_deduplication(self, manager):
        """Test that identical data doesn't create new versions"""
        # Create first version
        v1 = await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"value": "same"},
        )
        assert v1.version == 1

        # Try to create version with same data
        v2 = await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"value": "same"},
        )

        # Should return existing version, not create new one
        assert v2.version == 1
        assert v2.data_hash == v1.data_hash

    @pytest.mark.asyncio
    async def test_get_current_version(self, manager):
        """Test retrieving current version"""
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"v": 1},
        )
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"v": 2},
        )
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"v": 3},
        )

        current = await manager.get_version("proj1", "test_key")

        assert current is not None
        assert current.version == 3
        assert current.data == {"v": 3}

    @pytest.mark.asyncio
    async def test_get_specific_version(self, manager):
        """Test retrieving a specific version"""
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"v": 1},
        )
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"v": 2},
        )

        v1 = await manager.get_version("proj1", "test_key", 1)
        v2 = await manager.get_version("proj1", "test_key", 2)

        assert v1.version == 1
        assert v1.data == {"v": 1}
        assert v2.version == 2
        assert v2.data == {"v": 2}

    @pytest.mark.asyncio
    async def test_get_nonexistent_version(self, manager):
        """Test getting version that doesn't exist"""
        result = await manager.get_version("proj1", "nonexistent", 1)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_history(self, manager):
        """Test retrieving version history"""
        for i in range(5):
            await manager.create_version(
                project_id="proj1",
                data_key="test_key",
                data={"v": i},
            )

        history = await manager.get_history("proj1", "test_key", limit=3)

        assert history.data_key == "test_key"
        assert history.total_versions == 5
        assert history.current_version == 5
        assert len(history.versions) == 3
        # Should be newest first
        assert history.versions[0].version == 5
        assert history.versions[1].version == 4
        assert history.versions[2].version == 3

    @pytest.mark.asyncio
    async def test_get_history_with_offset(self, manager):
        """Test version history pagination"""
        for i in range(5):
            await manager.create_version(
                project_id="proj1",
                data_key="test_key",
                data={"v": i},
            )

        # Get versions 3 and 2 (skip 5 and 4)
        history = await manager.get_history(
            "proj1", "test_key",
            limit=2,
            offset=2
        )

        assert len(history.versions) == 2
        assert history.versions[0].version == 3
        assert history.versions[1].version == 2

    @pytest.mark.asyncio
    async def test_diff_versions(self, manager):
        """Test generating diff between versions"""
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"a": 1, "b": 2, "c": 3},
        )
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"a": 1, "b": 20, "d": 4},  # b changed, c removed, d added
        )

        diff = await manager.diff_versions("proj1", "test_key", 1, 2)

        assert diff is not None
        assert diff.from_version == 1
        assert diff.to_version == 2
        assert "d" in diff.added_keys
        assert "c" in diff.removed_keys
        assert "b" in diff.modified_keys
        assert "a" in diff.unchanged_keys
        assert diff.changes["b"]["from"] == 2
        assert diff.changes["b"]["to"] == 20

    @pytest.mark.asyncio
    async def test_diff_nonexistent_versions(self, manager):
        """Test diff with nonexistent versions"""
        result = await manager.diff_versions("proj1", "test_key", 1, 2)
        assert result is None

    @pytest.mark.asyncio
    async def test_restore_version(self, manager):
        """Test restoring a previous version

        Note: Due to deduplication, restoring to content that already exists
        will return the existing version rather than creating a new one.
        This test verifies the restore behavior and that the change_type
        is properly set to 'restore'.
        """
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"original": True, "v": 1},
        )
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"modified": True, "v": 2},
        )

        # Restore version 1 - this returns version 1 due to deduplication
        # (the content already exists as version 1)
        restored = await manager.restore_version(
            "proj1", "test_key", 1,
            restored_by="user1"
        )

        assert restored is not None
        # Content matches version 1
        assert restored.data == {"original": True, "v": 1}
        # The change_type should be updated to 'restore'
        assert restored.change_type == "restore"

    @pytest.mark.asyncio
    async def test_restore_version_creates_new(self, manager):
        """Test that restore creates new version when content differs from all existing"""
        # Create versions with distinct content that won't be deduplicated
        import time
        timestamp = time.time()

        v1 = await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"v": 1, "ts": timestamp},
        )
        v2 = await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"v": 2, "ts": timestamp + 1},
        )

        # Now delete version 1's hash mapping to simulate content no longer existing
        # (In real usage, restore would work when content is unique)
        base_key = f"contex:version:proj1:test_key"
        await manager.redis.delete(f"{base_key}:hash:{v1.data_hash}")

        # Now restore should create a new version
        restored = await manager.restore_version(
            "proj1", "test_key", 1,
            restored_by="user1"
        )

        assert restored is not None
        assert restored.version == 3  # New version created
        assert restored.data == {"v": 1, "ts": timestamp}
        assert restored.change_type == "restore"

    @pytest.mark.asyncio
    async def test_restore_nonexistent_version(self, manager):
        """Test restoring version that doesn't exist"""
        result = await manager.restore_version("proj1", "test_key", 999)
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_version(self, manager):
        """Test deleting a non-current version"""
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"v": 1},
        )
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"v": 2},
        )

        # Delete version 1 (not current)
        result = await manager.delete_version("proj1", "test_key", 1)

        assert result is True

        # Version 1 should be gone
        v1 = await manager.get_version("proj1", "test_key", 1)
        assert v1 is None

        # Version 2 should still exist
        v2 = await manager.get_version("proj1", "test_key", 2)
        assert v2 is not None

    @pytest.mark.asyncio
    async def test_cannot_delete_current_version(self, manager):
        """Test that current version cannot be deleted"""
        await manager.create_version(
            project_id="proj1",
            data_key="test_key",
            data={"v": 1},
        )

        result = await manager.delete_version("proj1", "test_key", 1)

        assert result is False

    @pytest.mark.asyncio
    async def test_get_version_count(self, manager):
        """Test getting version count"""
        for i in range(5):
            await manager.create_version(
                project_id="proj1",
                data_key="test_key",
                data={"v": i},
            )

        count = await manager.get_version_count("proj1", "test_key")
        assert count == 5

    @pytest.mark.asyncio
    async def test_version_count_empty(self, manager):
        """Test version count for nonexistent data"""
        count = await manager.get_version_count("proj1", "nonexistent")
        assert count == 0


class TestVersionCleanup:
    """Test automatic version cleanup"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    @pytest.mark.asyncio
    async def test_cleanup_old_versions(self, redis):
        """Test that old versions are cleaned up"""
        manager = VersionManager(redis, max_versions=3)

        # Create 5 versions
        for i in range(5):
            await manager.create_version(
                project_id="proj1",
                data_key="test_key",
                data={"v": i},
            )

        # Should only have last 3 versions
        count = await manager.get_version_count("proj1", "test_key")
        assert count == 5  # Count is the current version number

        # But old versions should be cleaned up
        v1 = await manager.get_version("proj1", "test_key", 1)
        v2 = await manager.get_version("proj1", "test_key", 2)
        v3 = await manager.get_version("proj1", "test_key", 3)
        v4 = await manager.get_version("proj1", "test_key", 4)
        v5 = await manager.get_version("proj1", "test_key", 5)

        # Versions 1 and 2 should be cleaned up
        assert v1 is None
        assert v2 is None
        # Versions 3, 4, 5 should exist
        assert v3 is not None
        assert v4 is not None
        assert v5 is not None


class TestVersionIsolation:
    """Test version isolation between projects and keys"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    @pytest_asyncio.fixture
    async def manager(self, redis):
        """Create a version manager"""
        return VersionManager(redis)

    @pytest.mark.asyncio
    async def test_isolation_between_projects(self, manager):
        """Test versions are isolated between projects"""
        await manager.create_version(
            project_id="proj1",
            data_key="shared_key",
            data={"from": "proj1"},
        )
        await manager.create_version(
            project_id="proj2",
            data_key="shared_key",
            data={"from": "proj2"},
        )

        v1 = await manager.get_version("proj1", "shared_key")
        v2 = await manager.get_version("proj2", "shared_key")

        assert v1.data == {"from": "proj1"}
        assert v2.data == {"from": "proj2"}
        assert v1.version == 1
        assert v2.version == 1

    @pytest.mark.asyncio
    async def test_isolation_between_keys(self, manager):
        """Test versions are isolated between data keys"""
        await manager.create_version(
            project_id="proj1",
            data_key="key1",
            data={"key": "1"},
        )
        await manager.create_version(
            project_id="proj1",
            data_key="key2",
            data={"key": "2"},
        )

        v1 = await manager.get_version("proj1", "key1")
        v2 = await manager.get_version("proj1", "key2")

        assert v1.data_key == "key1"
        assert v2.data_key == "key2"
        assert v1.version == 1
        assert v2.version == 1


class TestVersionGlobalInstance:
    """Test global instance management"""

    @pytest_asyncio.fixture
    async def redis(self):
        """Create a fake Redis client"""
        client = FakeAsyncRedis(decode_responses=False)
        yield client
        await client.flushall()
        await client.aclose()

    def test_init_version_manager(self, redis):
        """Test initializing global version manager"""
        manager = init_version_manager(redis, max_versions=50)

        assert manager is not None
        assert manager.max_versions == 50
        assert get_version_manager() is manager
