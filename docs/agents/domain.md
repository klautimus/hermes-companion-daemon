# Domain Docs Layout

**Layout**: Single-context

- `CONTEXT.md` at repo root — domain vocabulary for the project
- `docs/adr/` — Architecture Decision Records (create if needed)

Consumer rules: skills like `improve-codebase-architecture`, `tdd`, and `diagnosing-bugs` read `CONTEXT.md` for domain language and `docs/adr/` for settled decisions. Match the vocabulary in CONTEXT.md when writing code, tests, and plans.
