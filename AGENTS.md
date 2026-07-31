# Repository Guidelines

## Project Structure & Module Organization
- `backend/` holds the FastAPI app, Tencent ASR integration, and document-generation logic.
- `frontend/` holds the single-page UI: `index.html`, `app.js`, `styles.css`, and static assets.
- `backend/data/` is for runtime logs and local debug output only; do not commit generated audio, archives, or secrets.

## Build, Test, and Development Commands
- Run the backend locally:
  ```bash
  cd backend
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
  ```
- Check Python syntax:
  ```bash
  python -m py_compile backend/app/main.py backend/app/core/config.py backend/app/services/tencent_asr.py
  ```
- Check frontend syntax:
  ```bash
  node --check frontend/app.js
  ```
- Use `playwright-cli` for UI verification against `http://127.0.0.1:8000`.

## Coding Style & Naming Conventions
- Use 4-space indentation in Python and standard semicolon-free JavaScript.
- Keep names explicit and local to the project, such as `tencent_asr_*`, `render*`, `collect*`, and `apply*`.
- Keep comments short and practical, especially around ASR parsing, mode switching, and document generation.
- Preserve the existing Chinese UI copy unless the user asks for wording changes.

## Testing Guidelines
- There is no formal unit-test suite yet; rely on runtime checks.
- Verify backend startup, `/api/config`, realtime transcription, and document generation after each change.
- Use `/api/asr/debug` when you need ASR-specific troubleshooting.

## Commit & Pull Request Guidelines
- Use short imperative commit messages that match the existing history, for example `fix: improve realtime asr speaker diarization`.
- PRs should explain the visible change, list the verification commands used, and include screenshots for UI changes.

## Security & Configuration Tips
- Put secrets in `backend/.env` and keep `backend/.env.example` as the documented template.
- The ASR configuration is intentionally minimal; prefer the current `ASR_MODE` setting instead of adding new compatibility flags.
