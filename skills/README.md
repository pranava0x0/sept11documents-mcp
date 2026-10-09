# Skills

Instructions an AI assistant can load to use the `sept11` MCP server for one kind of question. Each
skill names the tool calls in order, the rules for the answer, and the points where the assistant
should stop and say what the archive cannot show.

| Skill | For a question like |
|---|---|
| [verify-a-quote](verify-a-quote/SKILL.md) | Is this quote really on that page? How do I cite it? |
| [building-records](building-records/SKILL.md) | What did the City file about my building? |
| [statements-and-records](statements-and-records/SKILL.md) | What did officials say in public, and what do the records show? |
| [proof-of-presence](proof-of-presence/SKILL.md) | What documents prove I was in Lower Manhattan? |
| [follow-the-money](follow-the-money/SKILL.md) | What happened to the money the City announced? |
| [release-watch](release-watch/SKILL.md) | What has the City released since a date, and what is due next? |

The server must be connected first (`python3 scripts/sept11_mcp.py configure <client>`). Claude Code
reads skills from `.claude/skills/` in a project or `~/.claude/skills/`; copy or link a folder there.
Other clients can paste a skill's text into the conversation. The server's prompts
(`research-plan`, `building-records`, `proof-of-presence`, `statements-and-records`,
`follow-the-money`) carry the same rules in shorter form.
