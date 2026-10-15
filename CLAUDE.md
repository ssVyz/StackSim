# CLAUDE.md

Guidance for Claude Code and other AI agents working in this repository.

## Project

`stacksim` is a Python project managed with [uv](https://docs.astral.sh/uv/). Python version is pinned in `.python-version`; dependencies and metadata live in `pyproject.toml`.

- Run: `uv run main.py`
- Add a dependency: `uv add <package>`

## Repository rules

1. **Do not modify this file (`CLAUDE.md`) unless explicitly instructed to.**
2. **Every code change gets a changelog entry and a patch version bump.**
   - Bump the patch version with `uv version --bump patch` (updates `pyproject.toml` and `uv.lock`).
   - Add a matching section to the top of `CHANGELOG.md`, following the existing Keep a Changelog format (`Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` / `Security`), dated with the current date.
   - The version in `pyproject.toml` and the newest `CHANGELOG.md` entry must always match.
   - One bump per task, not per edit: if a single task touches several files, it gets one version bump and one changelog section.
3. **Minor and major versions are only bumped when explicitly requested.** Never bump them on your own initiative, even for large or breaking changes — flag it to the user instead.
4. **Do not commit to git.** Commits (and pushes, tags, branches, rebases) are done by humans. Leave changes in the working tree for review.
