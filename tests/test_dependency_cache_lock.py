"""Exercise the existing audit-tool cache consumer, not just a dependency version string."""

from pathlib import Path

from cachecontrol.caches import FileCache


def test_audit_file_cache_remains_compatible_with_hardened_filelock(tmp_path):
    cache = FileCache(tmp_path)
    key = "https://audit.example.invalid/synthetic-metadata"
    cache.set(key, b"synthetic-cache-value")
    assert cache.get(key) == b"synthetic-cache-value"
    cache.set(key, b"updated-cache-value")
    assert cache.get(key) == b"updated-cache-value"
    cache.delete(key)
    assert cache.get(key) is None


def test_audit_cache_lock_does_not_truncate_a_preexisting_coordination_file(tmp_path):
    cache = FileCache(tmp_path)
    key = "https://audit.example.invalid/synthetic-metadata"
    lock_path = Path(cache._fn(key) + ".lock")
    lock_path.parent.mkdir(parents=True)
    lock_path.write_bytes(b"existing-coordination-file")
    cache.set(key, b"synthetic-cache-value")
    assert lock_path.read_bytes() == b"existing-coordination-file"
