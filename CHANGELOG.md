# Changelog

Each update ships as `touchline-vN-update.zip`, containing only the files that changed. Extract it into the project folder (the one with `README.md` in it) and choose **Replace** when asked.

## v7 (25 Oct): live on Azure
- Live app link in the README.
- `requirements-app.txt` pinned to the exact versions the deployed app and the tests run on.
- `.dockerignore` skips `.git`, so builds upload less.
- `docs/DEPLOY.md`: build the image in Azure straight from GitHub (no large upload from your PC), the environment created without a log workspace, the multi-factor sign-in fix, and how to update the live app.

## v6 (25 Oct): Azure OpenAI only
- Removed the GitHub Models route. GitHub retired GitHub Models on 30 July 2026, and its endpoint now answers "OK" instead of a model reply. The language model is Azure OpenAI in Microsoft Foundry.
- `python -m touchline.check`: one command to confirm the model answers through the Agent Framework workflow.
- A clear error message when a model endpoint returns something that isn't a model reply.
- `TOUCHLINE_LLM_MOMENTS` still lets you limit model calls to the big moments; it's now opt-in.
- Updated `.env.example`, README, `docs/DEPLOY.md` and the architecture diagram.

## v5 (25 Oct): recording helpers, docs, free model route
- Free language model through GitHub Models (`GITHUB_MODELS_TOKEN`). The co-commentator rations calls on the free tier.
- Player URL options for recording: `?at=<minute>`, `&voice=`, `&mode=`, `&pitch=1`.
- Play stays disabled until a match has loaded.
- Architecture diagram, screenshots, README polish, MIT licence.
- Tests are isolated from any model keys on the machine (`tests/conftest.py`).

## v4 (23 Oct): GitHub Models support
- `GITHUB_MODELS_TOKEN` as an alternative to Azure OpenAI.

## v3 (23 Oct): research desk
- `/research` page: season-wide questions with charts, tables and tool calls (`touchline/research.py`, `web/research.*`).
- Stricter Verifier: a number said next to a club or player must come from that row.

## v2 (23 Oct): Microsoft Agent Framework
- Co-commentator, Ask Touchline and the research desk run as Agent Framework agents in a Draft → Verify workflow (`touchline/agents.py`).
- Ask Touchline: questions by voice or text mid-match, using tools that see only the match so far.
- `touchline/prebuild.py`, `Dockerfile`, `docs/DEPLOY.md`, `requirements-app.txt`.

## v1 (20 Oct): first working Touchline
- State tracker, Describer, Director, Colour agent, Verifier, web player.
