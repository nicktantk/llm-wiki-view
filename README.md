# llm-wiki tree view

A read-only view of the [llm-wiki](https://gitcode.com/nicktantk/llm-wiki) case book, which
lives on GitCode. This repository holds **no copy of the wiki** — only the renderer and a
workflow that publishes its output to GitHub Pages.

```
GitCode (nicktantk/llm-wiki, public)
        │  REST, unauthenticated
        ▼
GitHub Actions — hourly + on demand
        │  build/render_tree.py --ref main
        ▼
GitHub Pages — one static page
```

## Why it pulls instead of being pushed to

The wiki changes when a human merges a Synthesis or Organize PR. GitCode cannot notify this
side without holding a GitHub token, so this side polls instead. That keeps the credential
count at zero: the wiki repo is public, so the renderer sends no token, and the workflow
writes only to this repository's Pages.

The cost is latency — a merge shows up within the hour, or immediately if you run the
workflow by hand from the Actions tab.

## What it renders

`build/render_tree.py` reads `wiki/index.md` for the project list, then each project's
Findings and the Incidents that back them, plus the `patterns`, `playbooks` and `principles`
vocabularies. Every entry links back to its source file on GitCode.

It takes the tree from any ref, or from a local checkout:

```bash
pip install -r requirements.txt
python build/render_tree.py --ref main --out index.html      # what Pages publishes
python build/render_tree.py --local ~/llm-wiki --out index.html
python build/render_tree.py --ref main --json tree.json      # the data, no HTML
```

## Setup, once

1. Settings → Pages → Source: **GitHub Actions**.
2. Actions → *Build the llm-wiki tree view* → **Run workflow**.

Nothing else: no secrets, no deploy key, no branch to maintain.
