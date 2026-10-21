# web/ (placeholder)

A minimal **Next.js chat UI** will live here. Not built yet.

Plan:
- `npx create-next-app@latest . --ts --app` (App Router, TypeScript)
- One page: chat box that `POST`s `{ message, history }` to the API's `/chat`
  (`NEXT_PUBLIC_API_URL`, default `http://localhost:8000`) and renders `reply`.
- Side panel: `GET /skills` (learned playbooks) and `GET /memory` (recent notes).
- Show which tools ran (`tool_calls`) and when a new skill was learned (`skill_saved`).

Until then, use the API's interactive docs at `http://localhost:8000/docs`.
