# Prompts

Version-controlled prompt files live in this directory.

Prompts are part of the software system: they must be reviewable through Git and
must not be embedded in Python functions (`docs/03_agent_behavior.md`). The
prompt set version is reported by `GET /api/v1/system/info` as `prompt_version`.

No prompts exist yet; LLM integration is implemented in a later phase.
