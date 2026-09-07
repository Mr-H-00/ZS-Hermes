# Ingestion and Reslice Flow

Status: Complete

Goal: route indexing and explicit reslice through one versioned Parent-Child write path while preserving the legacy single-chunk path.

## Minimum Tasks

- [x] Resolve `single_chunk` or `parent_child` from final processing parameters.
- [x] Create a PostgreSQL staging version before writing Milvus child records, then activate only after flush and read-back.
- [x] Expose an explicit reslice task and deduplicate equivalent requests with a Tasker fingerprint.
- [x] On failure, delete staging rows and any child records written for that version.
- [x] Add real PostgreSQL + Milvus integration coverage for success and rollback.

## Acceptance

- [x] The write path persists before the task is queued.
- [x] Reslice never replaces the existing active version until the new child data is verified.
- [x] Failure status is observable and retryable.

Owner: `MilvusKB.index_file`, `KnowledgeParentChildChunkRepository`, `KnowledgeBaseManager`, and `Tasker`.

Evidence: focused Parent-Child, Milvus, and router unit tests pass. `test_parent_child_flow_commits_verified_version_and_rolls_back_failed_replacement` verifies success and rollback against an isolated PostgreSQL schema and a real Milvus collection.
