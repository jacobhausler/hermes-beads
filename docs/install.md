# Install — two tiers

There are two tiers, and the difference is exactly one thing: whether Hermes
Desktop loads the workbench pane. The agent tools and the rich cards are
identical in both. Pick Tier 1 unless you specifically want the pane.

## Tier 1 — happy path (stock core; no pane, and no promise of one)

```sh
hermes plugins install jacobhausler/hermes-beads
hermes plugins enable hermes-beads
```

Restart the backend (`hermes serve`) so the tools mount. Requirements:

- **bd 1.3.0+**, configured by you — the operator — one of:
  - `bd` on `PATH`, or
  - `HERMES_BEADS_BD_BIN=/path/to/bd` in the backend's environment (the
    tools never take a binary path from the model)
  - [bd releases](https://github.com/steveyegge/beads/releases)
    (`brew install beads`, or grab a tarball; verify against `checksums.txt`)
- Hermes Agent ≥ 0.21 (stock — **no patched core required**)
- Python 3, stdlib only

What you get: the six `beads_*` agent tools (`beads_frontier`, `beads_show`,
`beads_claim`, `beads_update`, `beads_comment`, `beads_smoke`) and the rich
JSON they answer with. What you do **not** get: the desktop pane. Hermes
Desktop stays closed to this plugin — the pane's modules ship and pass their
`node --test` half, but the `@hermes/plugin-sdk` entry point that would mount
them is not part of this tier. Nothing arrives "helpfully" on its own here;
if you see a pane after only Tier 1, something is mislabeled and that's a
bug — please file it.

Machine-check this tier yourself (also runs in CI gate 2):

```sh
scripts/verify-tier1.sh
```

It enables the plugin into a throwaway `HERMES_HOME` under `$TMPDIR`
(never your real home, never the repo), proves the plugin and all six tools
mount through the real discovery path, proves no pane entry point shipped,
and greps the shipped docs for any false pane tell. Prints a JSON receipt
and cleans up after itself.

## Tier 2 — full features (OPTIONAL Hermes Desktop patch; documented only)

The pane modules under `desktop/` (`tree.mjs`, `search.mjs`, `blockers.mjs`,
`drafts_view.mjs`, …) are written for a host that injects their entire world:

- `reads: { searchRead, showRead, provider }` — the bounded read facade the
  search box and the blockers door run through; a missing member keeps that
  door present-but-disabled with a visible reason,
- `botView` — the bot door panel state (drafts land only through it),
- `telemetry` — the host-owned usage counters (pane-deletion rule),
- `bindRerender` — the host loader's live re-render handle.

The exact contract is `desktop/workbench.mjs:45-58`
(`WorkbenchApp({ snapshot, ui, controller, stack, session, storeInfo,
draftBeadId, botView, bindRerender, reads, telemetry })`).

Today **no `desktop/plugin.js` exists**: nothing registers through
`@hermes/plugin-sdk`, so Hermes Desktop does not load the pane, and this
repository ships no patch for it. Tier 2 is therefore a documented plan —
the patch below is a pinned proposal, **not an exercised, supported
installation**. The real entry point is a stated follow-up slice; until it
merges, do not apply this to a build you rely on.

**Version pin.** The snippet was written against Hermes Desktop
**v2026.9.24** — the release paired with the hermes-agent core this repo's CI
pins (`f97608f178d1ffeca59860195ab7da295f7c8e5f`). It is not expected to
survive a desktop version change unreviewed.

```js
// PROPOSAL ONLY — docs/install.md; this file is NOT shipped in this repo.
// desktop/plugin.js (Tier 2, Hermes Desktop v2026.9.24 pinned)
import { WorkbenchApp } from "./workbench.mjs";

export function register(host) {
  const reads = {
    searchRead: (q) => host.beadsSearch(q),   // host-injected bd reads
    showRead: (id) => host.beadsShow(id),
    provider: () => host.beadsStoreInfo(),
  };
  const telemetry = host.createTelemetry?.("hermes-beads");
  return {
    id: "hermes-beads-workbench",
    render(ctx) {
      return WorkbenchApp({
        ...ctx, reads, telemetry,
        botView: ctx.botView ?? null,
        bindRerender: ctx.bindRerender,
      });
    },
  };
}
```

Every member above is host-injected by design (AGENTS.md rule 4: the pane is
a view, zero I/O); the patch contains **no bd calls and no writes** — the
door stays behind `bd`.

**Update / reset restore notes.** This patch lives inside the desktop app's
own tree, so it is *expected to disappear*:

- After `hermes update` (or any desktop self-update), re-apply the pinned
  snippet above against the *new* version — re-check `workbench.mjs`'s
  injection contract and the version pin before relaunching.
- After a desktop reset/factory restore, the patch is gone; the plugin stays
  Tier 1 until you re-apply it deliberately.
- Losing the patch never breaks Tier 1: the agent tools and cards do not
  depend on the desktop at all.
