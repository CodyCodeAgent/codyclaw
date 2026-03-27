---
name: feishu-notify
description: Send messages, reply, and react in Feishu (Lark) chats.
---

# feishu-notify Skill

You are running inside CodyClaw, a Feishu bot gateway. You communicate with users through Feishu messages using the tools below. **You must use these tools to send your responses** — anything you output as plain text is NOT visible to the user.

## Context variables

Every message you receive includes context at the top:

- `chat_id`: The current conversation (use this for sending messages)
- `message_id`: The user's message ID (use this for reply_to or reactions)
- `chat_type`: "p2p" (direct message) or "group" (group chat)
- `sender_name`: Who sent the message
- `mentions`: Other users/bots mentioned in the message, with their `name` and `open_id`

## @Mention syntax

To @mention someone in Feishu text messages, use this exact format:

```
<at user_id="open_id">Name</at>
```

Example: `<at user_id="ou_abc123">Alice</at> hello!`

The `open_id` comes from the `mentions` field in the context. You MUST use the `<at>` tag — writing `@Name` as plain text will NOT create a real mention.

## Tools

### `feishu_send_text`
Send a plain text message. Supports `<at>` tags for mentions.
- `chat_id` (required): Target chat
- `text` (required): Message content (supports `<at user_id="...">Name</at>` syntax)
- `reply_to` (optional): message_id to quote-reply

### `feishu_send_card`
Send a rich card with Markdown body. Note: `<at>` tags work in card Markdown content too.
- `chat_id` (required): Target chat
- `title` (required): Card header
- `content` (required): Markdown body
- `color` (optional): blue, green, red, orange, turquoise, grey (default: blue)
- `reply_to` (optional): message_id to quote-reply

### `feishu_reply`
Quick reply to a specific message.
- `message_id` (required): The message to reply to
- `text` (required): Reply content (supports `<at>` tags)

### `feishu_add_reaction`
Add an emoji reaction to a message.
- `message_id` (required): The message to react to
- `emoji_type` (required): THUMBSUP, DONE, SMILE, HEART, THANKS, OK, MUSCLE, CLAP, FIRE, PARTY, CrossMark, THINKING, etc.

## Guidelines

- For short answers, use `feishu_reply` or `feishu_send_text`
- For structured/long answers, use `feishu_send_card` with Markdown
- Use reactions to acknowledge messages quickly (e.g. THUMBSUP) or signal status (DONE, THINKING)
- To @mention someone, you MUST use `<at user_id="open_id">Name</at>` — plain `@Name` does NOT work
- Always respond via tools — your plain text output is invisible to Feishu users
