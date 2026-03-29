---
name: skill-creator
description: Create, list, and manage skills to extend your own capabilities.
---

# skill-creator Skill

You can create new skills to teach yourself new capabilities. A skill is a set of instructions (in Markdown) that gets loaded into your system context on every future conversation.

## When to use

Use this when the user asks you to:
- Learn a new capability or workflow
- Remember a recurring pattern (e.g. "always format code reviews like this")
- Create a reusable template for a specific task
- Add specialized knowledge for a domain

## Available Tools

### `install_skill`
Create and install a new skill.

Parameters:
- `name` (required): Lowercase with hyphens (e.g. `code-reviewer`, `deploy-helper`)
- `description` (required): One-line summary of what the skill does
- `content` (required): The Markdown body — instructions, examples, guidelines

### `list_skills`
List all installed skills (built-in and user-installed).

### `remove_skill`
Remove a user-installed skill by name. Built-in skills cannot be removed.

Parameters:
- `name` (required): The skill name to remove

## Writing a good skill

A skill's `content` should include:

1. **When to activate** — describe the situations where this skill applies
2. **Instructions** — clear step-by-step guidance on what to do
3. **Examples** — show good vs bad output to calibrate behavior
4. **Constraints** — any rules, limits, or edge cases to handle

### Example: creating a "code-review" skill

```
name: code-review
description: Structured code review with security and performance checks.
content:
  # Code Review Skill

  ## When to activate
  When the user asks you to review code, a PR, or a diff.

  ## Review checklist
  1. **Correctness**: Does the logic match the intent?
  2. **Security**: SQL injection, XSS, command injection, secrets in code?
  3. **Performance**: N+1 queries, unnecessary allocations, missing indexes?
  4. **Readability**: Clear names, minimal nesting, no dead code?

  ## Output format
  - Start with a 1-sentence summary (approve / request changes)
  - List issues as bullet points with severity (critical / warning / nit)
  - End with what was done well
```

## Notes

- New skills take effect on the **next message** (not the current one)
- Skills are persistent — they survive restarts
- Keep skills focused: one skill = one capability
- Don't create skills that duplicate built-in skills (feishu-notify, cron-manager)
