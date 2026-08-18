# ctx-core

ctx-core is an open-source context system for Claude Code: a git-native,
human-readable markdown context corpus plus zero-dependency Python tooling
that packs the right context per task under a token budget, monitors its own
health, and manages context lifecycle without losing anything. Sibling of
[ctx-yield](https://github.com/Noctuem/ctx-yield), which measures whether an
existing context system is earning its tokens — ctx-core is the system
itself.

## Status

In build — spec-driven; watch this repo. The build spec lives outside this
repo until modules land here as they ship; this README and the rest of the
public surface will grow alongside the code.

## Quick start

```sh
git clone https://github.com/Noctuem/ctx-core.git
cd ctx-core
# tooling and CLI entry point land with module m1 (scaffold)
```

Nothing installable yet — this is the pre-m1 scaffold. Check back as modules
ship, or watch the repo for releases.
