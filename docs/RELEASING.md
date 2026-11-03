# Releasing rulewright

A PyPI version can never be reused or replaced, only yanked. Check everything below first.

## One-time setup (repository owner)

1. Rename the GitHub repository to `rulewright` (Settings, General). GitHub redirects the old
   address. Then `git remote set-url origin https://github.com/AbdulRehman19-bit/rulewright.git`.
2. On pypi.org, Your account, Publishing, add a **pending publisher**: project `rulewright`,
   owner `AbdulRehman19-bit`, repository `rulewright`, workflow `ci.yml`, environment `pypi`.
   The environment is created by GitHub on the first run; add a required reviewer to it if you
   want a manual approval before every publish.
3. Settings, Code security: enable **private vulnerability reporting** (SECURITY.md points to it).

## Before tagging

```
git status                      # clean, on main, pushed
uv run pytest -q && uv run ruff check . && uv run mypy rulewright
uv build && uv run --with twine twine check dist/*
```

- `version` in `pyproject.toml` and `__version__` in `rulewright/__init__.py` agree, and the new
  version is in `CHANGELOG.md`.
- The latest commit on main is green in GitHub Actions (Python 3.11, 3.12, 3.13).
- `curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/rulewright/<version>/json` prints
  404 (that version does not exist yet).

## Tag and publish

```
git tag -a v0.1.0 -m "rulewright 0.1.0"
git push origin v0.1.0
```

The `publish` job in `.github/workflows/ci.yml` runs after the tests pass, builds with `uv build`
and uploads with trusted publishing (no token is stored anywhere).

## After publishing

In a clean directory (not this repository):

```
pipx install rulewright           # or: uvx rulewright --help
rulewright --help
rulewright scan --repo <any git repository> --commits 200
```

It should print the command list and, for a repository with little agent history, the plain
"not enough yet" message. Then create a GitHub release from the tag and paste the CHANGELOG entry.

## If something is wrong after publishing

Yank the version on pypi.org (it stays installable by exact pin but is not chosen by default),
fix, bump the version (`0.1.1`) and release again. Do not try to re-upload the same version.

## The demo GIF

`docs/demo.tape` is a [VHS](https://github.com/charmbracelet/vhs) script. Run `vhs docs/demo.tape`
to produce `docs/demo.gif`, then put `![demo](docs/demo.gif)` under the README title.
It needs `GROQ_API_KEY` set and a clone of a repository with a lot of agent history (the tape
uses `../litellm`; clone it first with `git clone --depth 1500 https://github.com/BerriAI/litellm`).
Without VHS, ScreenToGif (Windows) works: run the same commands and trim the waiting time.
Use `--commits 500` for a short run; the first run downloads the embedding model (about 90 MB),
so do one throwaway run first.
