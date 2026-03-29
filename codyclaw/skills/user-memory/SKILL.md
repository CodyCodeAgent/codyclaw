---
name: user-memory
description: Remember and recall information about individual users across all conversations.
---

# user-memory Skill

You have a persistent memory for each user you interact with. This memory follows the user across all groups, all agents, and all sessions. Use it to be a better colleague.

## When to save

Save a memory PROACTIVELY when you learn:
- **Identity**: name, role, team, department, responsibilities
- **Preferences**: communication style, language, response format (tables vs bullets vs prose)
- **Context**: current projects, ongoing tasks, deadlines they mentioned
- **Relationships**: who they collaborate with, who they report to, who they manage
- **Patterns**: recurring requests, common workflows, things they ask about often

Do NOT save:
- Trivial information ("user said hi")
- Things already in their profile
- Sensitive data (passwords, tokens, personal ID numbers)

## Tools

### `save_user_memory`
Save a note about a user. One fact per call, concise.

- `user_id` (required): The user's open_id (from `[Feishu context]` sender_id)
- `content` (required): The note (e.g. "Prefers responses in English", "Works on backend team, main project is order-service")

### `get_user_memory`
Retrieve all saved notes about a user.

- `user_id` (required): The user's open_id

## Examples

```
# First interaction with a new user:
[User profile]
(empty — you don't know this person yet)

小明: 帮我看看 order-service 的日志，我是后端组的

→ save_user_memory(user_id="ou_xxx", content="后端组，负责 order-service")
→ Then answer the question
```

```
# Next interaction (even in a different group):
[User profile]
- 后端组，负责 order-service

小明: 帮我写个周报

→ You already know their context, can write a relevant weekly report
```
