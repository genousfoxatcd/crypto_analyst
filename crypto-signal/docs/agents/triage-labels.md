# Triage Labels

Five canonical states used by the `/triage` skill.

These map to the `status:` frontmatter field in `.scratch/*/issue.md` files.

| Role | Label string | Meaning |
|------|-------------|---------|
| Needs evaluation | `needs-triage` | Maintainer needs to review and categorise |
| Waiting on reporter | `needs-info` | Blocked — need more information from the requester |
| Ready for agent | `ready-for-agent` | Fully specified, AFK-ready; an agent can pick it up with no human context |
| Ready for human | `ready-for-human` | Needs human judgment or implementation |
| Won't fix | `wontfix` | Acknowledged but will not be actioned |
