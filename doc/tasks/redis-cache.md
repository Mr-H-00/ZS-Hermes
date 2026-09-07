# Redis cache

Status: implemented (unit and real Redis integration evidence)

Goal: cache only disposable Parent-Child parent text and query results. PostgreSQL and Milvus remain the source of truth.

## Minimum tasks

- [x] Define scoped keys containing `kb_id`, version scope, and object identity.
- [x] Integrate parent text and query-result caches.
- [x] Invalidate cache entries on activation, deletion, and reslice.
- [x] Fail open on misses, expiry, and Redis errors.
- [x] Add unit coverage for key generation, round trips, and scoped invalidation.

## Acceptance

- [x] Redis affects performance only, never final business truth.
- [x] Cache misses fall back to PostgreSQL/Milvus reads.
- [x] Expired or malformed values are treated as misses.

Evidence: `docker compose exec -T api uv run --no-sync --group test pytest test/unit/knowledge/test_parent_child_cache.py test/unit/knowledge/test_milvus_parent_child_index_flow.py::test_parent_child_activation_invalidates_old_parent_when_projection_cleanup_fails test/unit/plugins/test_milvus_kb.py::test_cleanup_database_resources_offloads_milvus_cleanup test/unit/plugins/test_milvus_kb.py::test_delete_file_invalidates_parent_child_cache_after_metadata_delete -q` (9 passed).

Real Redis evidence: `docker compose exec -T api uv run --no-sync --group test pytest test/integration/knowledge/test_parent_child_cache.py -q` (1 passed).

The cache unit suite covers the distinct `3600s` parent-text and `600s` query TTLs, malformed JSON and malformed list entries as misses, and Redis read/write/delete errors as fail-open cache operations. Deletion and activation wiring is covered with executor unit tests. The Redis integration test verifies stored TTLs, round trips, scoped invalidation, and cleanup against the Compose Redis service.
