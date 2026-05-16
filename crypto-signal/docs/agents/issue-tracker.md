# Issue Tracker: Local Markdown

Issues and tasks live as markdown files under `.scratch/` in this repo.

## Layout

```
.scratch/
  <feature-or-bug-slug>/
    issue.md        ← the issue itself
    notes.md        ← optional scratch notes
    *.md            ← any other relevant files
```

## Creating issues

Use `/to-issues` to convert a description into one or more `.scratch/<slug>/issue.md` files.

Each file uses this frontmatter:

```markdown
---
title: Short issue title
status: needs-triage
created: YYYY-MM-DD
---

Issue body here.
```

## Triaging

Use `/triage` to update the `status:` frontmatter field in each issue file.

## Closing issues

Move the file to `.scratch/.done/<slug>/issue.md` or delete it when resolved.
