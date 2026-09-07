# Frontend Parent-Child UI

Status: implemented (full unit/lint/build evidence; real browser probe not run)

Goal: expose Parent-Child configuration and child hit provenance without changing legacy result compatibility.

## Minimum tasks

- [x] Show or hide sparse controls according to embedding model capability.
- [x] Add Parent-Child toggle and parameter inputs to creation.
- [x] Show persisted Parent-Child state and parameters on the detail view.
- [x] Show vector fusion and RRF controls conditionally.
- [x] Show parent text with child hit identifiers and offsets while preserving legacy results.
- [x] Add web unit, lint, and build evidence.

## Acceptance

- [x] Non-BGE-M3 models cannot submit sparse configuration.
- [x] Raw weights remain visible as configured.
- [x] Results distinguish parent results from child hits.

Evidence: `docker compose run --rm --no-deps -T -v "${PWD}\\web\\test:/app/test:ro" web pnpm run test:unit` (178 passed), `docker compose exec -T web pnpm run lint:check`, and `docker compose exec -T web pnpm run build` all pass. The read-only test mount ensures the temporary container executes the current host tests instead of the image's older test directory. The runtime SFC tests verify model capability switching, conditional query fields, explicit reslice task submission, Parent-Child result provenance, and legacy rendering.

Browser verification: Not run. CUA returned no available browser and the in-app browser provider was unavailable, so no page screenshot is claimed.
