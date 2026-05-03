# Research Assistant – Deliverables (Unified Project)

This file lists the **deliverables for the Research Assistant portion only** (not the ORBS grader features), in the unified codebase.

## Deliverables

- **Unified app (Research Assistant + ORBS grader in one backend)**
  - The project contains both the research assistant workflows and the notebook/grader UI served from the same FastAPI backend.

- **Session-scoped OpenRouter API key support (no hardcoded keys)**
  - Users can **enter an OpenRouter API key per session**.
  - The key is stored **only in server memory** for that session and is **deleted when the session ends** (and also expires automatically after inactivity).
  - No API key is written to disk, stored in SQLite, or embedded in frontend build artifacts.

- **Multi-user safe session isolation**
  - Chat history and notebook upload state are **isolated per session** (cookie-based session id) rather than shared as a single global process state.

- **Research paper ingestion**
  - Upload a PDF and extract text/markdown via MinerU processing.
  - Store processed paper content and extracted points in `research.db`.

- **Research point generation**
  - Generate 5–7 research points for a user prompt, grounded in stored papers.

- **Research discussion generation**
  - Produce an academic-style discussion synthesizing the available paper corpus, with citations/indices.

- **Database browsing**
  - View stored paper records and inspect their extracted points and content previews from the Research Assistant UI.

## What is intentionally not a deliverable here

- ORBS grading features and their specific evaluation logic are **excluded** from this deliverables list (even though they live in the unified project).

