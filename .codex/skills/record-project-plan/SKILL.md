---
name: record-project-plan
description: Use when the user asks to record, save, 整理, document, or write down discussed requirements, plans, decisions, or implementation steps for this poly-auto-trading project. Records should be saved under requirements/ using sequential stepN.plan.md files.
---

# Record Project Plan

When the user asks to record project requirements, plans, decisions, or follow-up development notes, save them in `requirements/` using this naming pattern:

```txt
requirements/stepN.plan.md
```

## Workflow

1. Inspect existing files matching `requirements/step*.plan.md`.
2. Choose the next integer `N` after the largest existing step number.
3. Create a concise Markdown plan file with:
   - Title
   - Background or context
   - Requirements or decisions
   - Implementation phases or tasks
   - Acceptance criteria when applicable
   - Open questions when applicable
4. Do not overwrite existing `stepN.plan.md` files unless the user explicitly asks to revise that exact file.
5. Keep `requirements/report-requirements.md` as the long-lived product requirements document; use `stepN.plan.md` files for dated or conversation-derived plans.

## Defaults

- Use Chinese when the conversation is in Chinese.
- Prefer implementation-ready bullets over broad prose.
- If the user says “记录一下”, “整理进去”, “保存这个计划”, or similar, apply this workflow.
- If the user names an exact file, follow the named file instead of auto-incrementing.

