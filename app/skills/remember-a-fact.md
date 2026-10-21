---
name: remember-a-fact
description: Save something the user tells you about their life so it can be recalled later
triggers: remember, don't forget, note that, keep in mind
---

1. Rewrite the fact as one self-contained sentence about the user (who, what, when).
2. Keep any condition the user attached ("on weekdays", "for work trips").
3. Call `remember` with that sentence and a short `kind` (person, preference, plan, project, note).
4. Confirm back in one short line.
