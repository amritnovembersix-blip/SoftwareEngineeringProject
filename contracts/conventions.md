# API Conventions

Status: DRAFT · Author: Justin Keck · Owner: TBD · Last changed: 2026-09-28

These apply to every HTTP API in SASS unless a contract says otherwise.

## Naming

- JSON field names: `snake_case` (e.g. `submission_id`, `cached_due_date`).
  Matches Canvas, Python, and SQL. Spring Boot: `spring.jackson.property-naming-strategy=SNAKE_CASE`.
- Enum values: lowercase `snake_case` (e.g. `on_grade`, `submission_review`).
- URL paths: lowercase, hyphen-separated (e.g. `/review-policy`), except where mirroring Canvas.

## Types

- **IDs are strings** in JSON, even when numeric in the source system.
- **Timestamps** are ISO 8601 in UTC with a `Z` suffix: `2026-10-05T14:30:00Z`.
- **Dates** without a time are `YYYY-MM-DD`.
- A missing optional value is `null`, not an empty string.

## Status codes

| Code | Meaning |
|---|---|
| `200` | OK |
| `202` | Accepted for asynchronous processing. The body includes a `run_id` to poll |
| `401` | Missing or invalid credentials |
| `403` | Authenticated but not allowed (e.g. a student accessing another student's data) |
| `404` | Not found |
| `422` | Validation failed |

## Errors

Every non-2xx response uses this body:

```json
{
  "error": {
    "code": "invalid_cron",
    "message": "Human-readable explanation",
    "details": {}
  }
}
```

- `code`: stable, machine-readable, lowercase `snake_case`.
- `message`: for humans. It may change.
- `details`: optional structured context (e.g. which field failed).

## Pagination (Canvas-shaped APIs)

- `per_page` (default 10, max 100) and `page` (1-based) query parameters.
- The next and previous pages are given in the `Link` response header (`rel="next"`, `rel="prev"`), as Canvas does.
