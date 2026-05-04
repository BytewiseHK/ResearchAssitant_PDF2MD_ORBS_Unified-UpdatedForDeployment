# Research Assistant – Deliverables (Unified Project)

This file lists the **deliverables for the Research Assistant portion only** (not the ORBS grader features), in the unified codebase, including recent deployment and UX updates.

## Deliverables

- **Unified app (Research Assistant + ORBS grader in one backend)**
  - The project contains both the research assistant workflows and the notebook/grader UI served from the same FastAPI backend.

- **Session-scoped OpenRouter API key support (no hardcoded keys)**
  - Users can **enter an OpenRouter API key per session**.
  - The key is stored **only in server memory** for that session and is **deleted when the session ends** (and also expires automatically after inactivity).
  - No API key is written to disk, stored in SQLite, or embedded in frontend build artifacts.

- **Multi-user safe session isolation**
  - Chat history and notebook upload state are **isolated per session** (cookie-based session id) rather than shared as a single global process state.

- **Research paper ingestion (MinerU via cloud API)**
  - Upload a PDF and extract text/markdown via **MinerU.net** (managed PDF→Markdown service), not a heavyweight local ML stack on the server.
  - Store processed paper content and extracted points in `research.db`.

- **Research point generation**
  - Generate 5–7 research points for a user prompt, grounded in stored papers.

- **Suggest sources (abstract-based ranking)**
  - Optional **Suggest sources** flow ranks papers for checkbox selection using **only** each paper’s parsed abstract (or a short opening excerpt if no abstract block exists).
  - The backend asks the configured LLM (OpenRouter) for `include`, **score (0–100)**, and a short note per paper; scores reflect **model-judged topical relevance** to the user’s prompt, then the UI sorts by score. This is **not** a bibliometric or citation-based metric.

- **Research discussion generation**
  - Produce an academic-style discussion synthesizing the available paper corpus, with citations/indices.

- **Database browsing**
  - View stored paper records and inspect their extracted points and content previews from the Research Assistant UI.

- **Frontend deployment and same-origin API access (e.g. Vercel + Render)**
  - Static UI can be served from a host such as **Vercel** while JSON APIs are proxied to **Render** using root [`vercel.json`](vercel.json) **`rewrites`** (plus legacy `routes`), so the browser keeps **same-origin** requests and **session cookies** work with `credentials: 'include'`.
  - Operational verification steps are summarized in [`docs/vercel-proxy-verify.md`](docs/vercel-proxy-verify.md).

## UI consistency (home, ORBS grader, Research Assistant)

The landing and sub-apps were aligned so the experience feels like **one Bytewise product family**:

- **Shared visual language:** dark surfaces (`#121212` / `#1e1e1e`), card-style panels, subtle borders and shadows, **Font Awesome** icons, and a consistent **light gray accent gradient** strip (e.g. home hero bar; echoed in Research styling via [`frontend/static/styleResearch.css`](frontend/static/styleResearch.css)).
- **Shared branding:** **Bytewise Applications** on the app picker ([`frontend/index.html`](frontend/index.html)); **Bytewise ORBS Grader** and **Bytewise Research Assistant** as clear sub-titles on their respective pages.
- **Navigation:** Research Assistant includes an **Apps home** link back to `/app/` so users can move between ORBS and Research without losing the mental model of a single hub.
- **Interaction patterns:** Primary actions use similar button treatments; Research uses session key handling (modal + “Save for session”) in line with the ORBS pattern for API keys where applicable.

Together, these changes make the **home page → ORBS grader → Research Assistant** path cohesive rather than three unrelated skins.

## Deployment architecture: MinerU cloud vs. on-server processing

Earlier iterations tied **MinerU-style PDF processing to the backend machine**, which implied **large RAM and GPU/CPU** budgets suitable for local inference or heavy native pipelines—**costly and fragile** on small cloud instances (e.g. Render’s typical tiers).

The current approach uses **MinerU’s hosted product (MinerU.net)** via [`backend/mineru/mineru_net_client.py`](backend/mineru/mineru_net_client.py): the FastAPI service **uploads the PDF, polls the cloud job, and stores Markdown results**. The Render host only needs enough memory for **FastAPI, SQLite, and HTTP clients**—on the order described for the Docker setup (**~512MB–1GB** for the API process in [`DOCKER_README.md`](DOCKER_README.md)), not multi‑GB ML workloads.

That shift is what makes **practical deployment on Render (and similar small VMs)** realistic without provisioning an ML-grade server for every PDF.

## Internship context: Alibaba Cloud attempt

During the internship, **deploying the Research Assistant on Alibaba Cloud was explored**, but **no clarifications or actionable guidance** came back from the team that had been suggested for consultation. Work proceeded using alternative hosts (e.g. Render + Vercel) where requirements and limits were clearer.

## What is intentionally not a deliverable here

- ORBS grading features and their specific evaluation logic are **excluded** from this deliverables list (even though they live in the unified project).

## Notes for future development

- **Discussion tab and RAG:** The current **discussion** experience does **not** use retrieval-augmented generation (RAG) over chunks of the corpus; it operates on the broader “discussion” prompt path already in the app. **Introducing RAG** (chunking papers, embeddings, top‑k retrieval before generation) is a strong next step for **more grounded, citeable answers** and lower hallucination risk in long threads.

- **Exa AI:** **[Exa](https://exa.ai)** (neural / semantic search over the web and datasets) could be a valuable integration for **discovering or validating external papers and sources** beyond the user’s uploaded library, complementing the in‑DB tools already in the Research Assistant.
