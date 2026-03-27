# feishu-notify Skill

## Description

This skill enables the Cody Agent to send notifications to Feishu (Lark) chat sessions directly.

## Usage

Use this skill when you need to proactively send a message or notification to a Feishu user or group chat.

## Examples

- "Send a message to the ops group saying the deployment is complete"
- "Notify the user that the background task has finished"
- "Post a summary of the analysis results to the chat"

## Parameters

- `chat_id` (required): The Feishu chat ID (user open_id for direct messages, or group chat_id for group chats)
- `message` (required): The text content to send
- `message_type` (optional): One of `"text"` or `"card"`. Defaults to `"text"`.

## Notes

- The skill uses the CodyClaw channel layer to send messages, so the bot must be a member of the target chat.
- For group chats, ensure the bot has been added to the group before using this skill.
- Card messages support Markdown formatting.
