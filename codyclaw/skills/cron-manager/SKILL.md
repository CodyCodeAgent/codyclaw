---
name: cron-manager
description: Create, list, and delete scheduled cron tasks that run Agent prompts on a recurring schedule.
---

# cron-manager Skill

## When to use

Use this skill when the user wants to:
- Schedule a recurring task (e.g. "remind me every morning", "check logs every hour")
- List existing scheduled tasks
- Cancel or delete a scheduled task

## Available Tools

### `create_cron_task`
Create a new scheduled task.

Parameters:
- `task_id` (required): Unique identifier, lowercase with hyphens (e.g. `daily-report`)
- `name` (required): Human-readable name (e.g. "Daily Report")
- `agent_id` (required): Which agent executes the task — ask the user or use the current agent's ID
- `prompt` (required): The instruction the agent will run on each execution
- `schedule` (required): Cron expression (`"0 9 * * 1-5"`) or interval (`"every 30m"`, `"2h"`, `"60"`)
- `notify_chat_id` (optional): Feishu chat ID to send results to; use the current chat if not specified

### `list_cron_tasks`
List all currently scheduled tasks with their next run time.

No parameters required.

### `delete_cron_task`
Delete a scheduled task by its ID.

Parameters:
- `task_id` (required): The task ID to delete

## Examples

- "Remind me every weekday at 9am to check emails" → create task with schedule `"0 9 * * 1-5"`
- "Check server health every 30 minutes" → create task with schedule `"every 30m"`
- "Show me all scheduled tasks" → list_cron_tasks
- "Cancel the daily-report task" → delete_cron_task with task_id `"daily-report"`

## Notes

- Tasks created here are in-memory and will be lost if the gateway restarts.
- The `schedule` field supports standard 5-field cron expressions or simple intervals: `"every Xm"`, `"every Xh"`, `"Xm"`, `"Xh"`.
- Always confirm the schedule and notify chat with the user before creating.
