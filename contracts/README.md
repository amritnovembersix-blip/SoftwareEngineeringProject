# contracts/

A **contract** is the written agreement for a boundary between two parts of the system:
the endpoints, the request and response fields, and the errors. Both sides build against the
contract, so work can proceed in parallel.

## Files

| File | Boundary |
|---|---|
| `conventions.md` | Rules shared by every API |
| `agentic-api.yaml` | Callers (mock LMS webhook, backend, admins) → agentic service |
| `mock-lms-api.yaml` | Agentic service → mock LMS (Canvas-shaped), plus the outbound webhook |
| `../db/migrations/` | Everyone → Postgres (the schema is a contract too) |

## Status legend

Every contract starts with `Status: … · Author: … · Owner: … · Last changed: …`.

- **DRAFT**: proposed. Written by the consumer. May change freely after discussion.
- **ADOPTED**: the owner has accepted it. Changes follow the rules below.
- Items marked `planned` (`x-status: planned` in OpenAPI) are designed but not yet built.

## Change rules

1. Contracts change only through a PR that edits the contract file.
2. The PR tags the people on the other side of the boundary.
3. **Adding** an optional field or a new endpoint is safe.
4. **Renaming or removing** anything is breaking. Warn the other side before merging.
5. Update `Last changed:` in the file header.

## Lint

```bash
npx --yes @redocly/cli@1 lint contracts/agentic-api.yaml contracts/mock-lms-api.yaml
```
