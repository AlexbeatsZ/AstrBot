# Domain Docs

This repository uses a single-context domain documentation layout. Engineering skills should consume domain documentation as follows.

## Before exploring

- Read `CONTEXT.md` at the repository root when it exists.
- If `CONTEXT-MAP.md` exists instead, follow it to each context relevant to the task.
- Read applicable decisions in `docs/adr/` and context-scoped `src/<context>/docs/adr/` directories.

If these files do not exist, proceed silently. Domain documentation is created lazily when terminology or architectural decisions need to be recorded.

## Vocabulary

Use terms as defined in `CONTEXT.md` in issue titles, hypotheses, tests, and implementation notes. Avoid synonyms that the glossary explicitly rejects.

If a required concept is absent, reconsider whether the codebase already uses another term. Record a genuine domain-language gap for a later documentation session.

## Architectural decisions

Surface any conflict with an existing ADR explicitly instead of silently overriding it.
