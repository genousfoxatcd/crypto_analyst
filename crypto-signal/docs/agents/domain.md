# Domain Docs

Layout: **single-context**

## Files

- `CONTEXT.md` — project domain language, key concepts, naming conventions, abbreviations
- `docs/adr/` — architectural decision records (one file per decision)

## Consumer rules

- Read `CONTEXT.md` at the start of every `/diagnose`, `/tdd`, `/improve-codebase-architecture`, and `/zoom-out` session.
- If `CONTEXT.md` does not exist yet, the first run of `/grill-with-docs` will create it.
- Check `docs/adr/` for past decisions before proposing architecture changes. If no ADRs exist yet, proceed without — but propose one after making a significant decision.
- This is a single-context repo: there is no `CONTEXT-MAP.md`. One `CONTEXT.md` covers the whole project.
