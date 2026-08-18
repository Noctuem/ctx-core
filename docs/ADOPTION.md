# Adoption

Two checklists: confirming a fresh clone is genuinely ready to customize, and — for
anyone maintaining this project rather than just using it — listing it where other
people can find it.

## 1. Confirming a fresh clone is still in template state

A clone straight from the template ("Use this template" on GitHub, or a plain `git
clone`) should be in an easy-to-recognize starting state: nothing customized yet,
nothing broken. Two things define that state, both checked by
`ctx_core.layout.Layout.is_template_state()`:

1. `core/profile.md` is still byte-identical to the shipped placeholder
   (`<!-- run \`ctx init\` -->`) — nobody has run the interview yet.
2. `notes/` holds nothing but housekeeping files (`.gitkeep`) — no domain-specific
   content has been added yet.

You can check this yourself two ways:

- **`ctx doctor`** — if the corpus is still in template state, doctor reports it as a
  **soft warning** ("run `ctx init` to customize it"), never a hard failure. A fresh
  clone is expected to look this way; doctor just makes sure you notice, once, and
  never confuses "not yet customized" with "broken."
- **In code** — `Layout(root).is_template_state()` returns `True`/`False` directly, if
  you're scripting a bootstrap check (e.g. in CI, to confirm a newly-generated clone
  came up clean before running anything against it).

Once you run `ctx init` and/or add a real note under `notes/`, this state clears
permanently for that clone — there's no way back to "template state" short of starting
from a fresh clone.

## 2. Listing this plugin where people can find it

ctx-core's `.claude/` directory (see `.claude/README.md`) is a native Claude Code
plugin: skills, a slash command, and a hook. Getting it in front of people who don't
already know about this repo means listing it in the places the Claude Code community
actually looks. This is a **checklist for whoever maintains this project**, not
something the repo does automatically — deciding *when* to submit and *who* submits is
a maintainer call, left open deliberately.

- [ ] **Claude Code plugin marketplace.** Package `.claude/` per the marketplace's
      current submission format (check the marketplace docs for the manifest shape —
      it's evolved since this checklist was written) and submit it.
- [ ] **[`awesome-claude-code`](https://github.com/hesreallyhim/awesome-claude-code).**
      Open a PR adding this repo under the appropriate category (context/memory
      tooling), following that list's own contribution guidelines.
- [ ] **This README's "Status" section.** Once either listing lands, update the status
      line here to point at it, so a reader arriving from either place can tell at a
      glance this is a maintained, listed project rather than an orphaned clone.
- [ ] **PyPI.** `pip install ctx-core` only works once a release has actually been
      published (`python -m build && twine upload`, or an equivalent CI release job).
      Confirm the package is live before pointing anyone at the quick-start's `pip
      install` step in anger.

None of these four steps block using ctx-core from a local clone — they're entirely
about discoverability for people who haven't found this repo yet.
