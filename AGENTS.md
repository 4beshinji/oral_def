# Repository Guidelines

## Project Structure & Module Organization

The application lives in `oral-defense-seed/`. Its `backend/app/` contains FastAPI endpoints, SQLite persistence, conversation logic, and text/speech providers. `frontend/src/` contains React/TypeScript screens and styles. Tests live in `backend/tests/` and `frontend/tests/`. Shared prompts, sample inputs, and model metadata belong in `prompts/`, `examples/`, and `models/manifest.json`.

Read `docs/adr/0001-coach-led-shadowing.md`, `docs/DESIGN.md`, and `docs/IMPLEMENTATION.md` before changing conversation behavior; adopted requirements and implemented features still differ.

## Build, Test, and Development Commands

Run these commands from `oral-defense-seed/`. Use Python 3.12–3.13, a supported Node version starting at 22.12, and the committed lockfiles. Manage Python with `uv`; do not install into system Python with bare `pip`.

- `uv sync --locked`: install Python dependencies, including development tools.
- `npm --prefix frontend ci`: install locked frontend dependencies.
- `npm --prefix frontend run build`: type-check and build the frontend.
- `uv run --locked uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`: serve the API and built UI; use a single worker.
- `npm --prefix frontend run dev`: start Vite on port 5173, proxying `/v1` to the API.
- `uv run --locked pytest -q`: run backend tests.
- `npm --prefix frontend run test:e2e`: run Playwright after building the UI.

## Coding Style & Naming Conventions

Use four-space Python indentation, snake_case functions/modules, and PascalCase classes. Ruff targets Python 3.12 with a 100-character line limit. Run `uv run --locked ruff check backend scripts` and `uv run --locked ruff format --check backend scripts`.

Follow existing TypeScript formatting: two spaces, double quotes, semicolons, PascalCase components, and camelCase functions. Prettier is available in frontend development dependencies.

## Testing Guidelines

Use pytest files named `test_*.py` and Playwright files named `*.spec.ts`. Cover changed persistence, conversation transitions, provider failures, and user interactions with isolated fixtures. No numeric coverage threshold is configured.

E2E requires port 8000 to be free and uses `.cache/e2e-data/`. Chrome defaults to `/usr/bin/google-chrome`; override with `CHROME_BIN`. Mock providers and virtual microphones do not validate real model quality or pronunciation.

## Commit & Pull Request Guidelines

There is no commit history yet. Use concise, imperative commit subjects. PRs should describe behavior changes, link relevant issues or design documents, and report validation commands/results. Include screenshots for UI changes.

## Configuration & Local Data

Use `.env.example` as a reference; `.env` is not loaded automatically. Keep credentials, recordings, databases, and downloaded speech models out of Git. Verify device visibility and framework compatibility before GPU work.
