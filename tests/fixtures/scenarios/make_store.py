"""Disposable scenario store for tests/test_scenarios.mjs ().

Reuses tests/fixtures/e2e-runtime/bootstrap.py verbatim — make_store, actor,
seed_bead, raw_bd (--readonly for every readback), show_dict, comments_list.
Nothing here mocks bd: every command drives the ACTUAL pinned binary
(v1.3.0, f45b249ce) against disposable stores the scenario runner owns.
The planning store is never touched; deletion is only ever `bd delete
--force` on a store this file created.

CLI (called by the node runner via child_process):

  python3 make_store.py seed [--prefix scn]
      create one fully-seeded scenario store; print
      {"store": path, "storeInfo": {workspace, db}, "ids": {...}}

  python3 make_store.py read <store> <name> [args...]
      fixed-argv READ through bootstrap.raw_bd(readonly=True); print the
      parsed JSON payload (or {"rc":n,"stdout":...,"stderr":...} when the
      read is expected to be able to fail, e.g. show of a deleted id).

  python3 make_store.py act <store> <actor> <argv...>
      WRITE passthrough (create/update/dep/comment/delete/reclaim...) with
      the given --actor; always prints {"rc","stdout","stderr"} — nonzero
      rc is a RESULT the scenario asserts, never a crash.

  python3 make_store.py cleanup <store>
      remove a store directory created by this file.
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "e2e-runtime"))
import bootstrap as b  # noqa: E402


def seed_world(prefix="scn"):
    """The S-world: epic rail, branch A/B, blocked+ready+claimed rows, the
    conflict target, the dead-worker task, and the multi-blocker inheritance
    chain. All creation is bd-native (create / dep add / update --parent)."""
    store = b.make_store(prefix=prefix)
    ids = {}

    def create(title, **kw):
        return b.seed_bead(store, title, **kw)

    def parent(child, par):
        p = b.raw_bd(store, "update", child, "--parent", par, "--json",
                     actor_name="lab-seeder")
        assert p.returncode == 0, p.stderr

    # epic rail: real --type epic (label-only epics do NOT count in
    # `bd epic status` — probed), children via the parent FIELD.
    epic = json.loads(b.raw_bd(
        store, "create", "Hermes Beads laboratory", "--type", "epic",
        "--json", actor_name="lab-seeder").stdout)["id"]
    ids["epic"] = epic
    pa = create("branch A")
    pb = create("branch B")
    parent(pa, epic)
    parent(pb, epic)
    ids["branchA"] = pa
    ids["branchB"] = pb

    gate_a = create("branch A gate")
    parent(gate_a, pa)
    ids["gateA"] = gate_a
    # S3/S2 protagonist: stored status stays `open` while natively blocked.
    task_b = create("branch B blocked task", deps=(gate_a,))
    parent(task_b, pb)
    ids["taskB"] = task_b

    ready_task = create("plain ready task")
    parent(ready_task, pb)
    ids["ready"] = ready_task

    # Mine tab: an already-claimed row (native ready excludes claimed —
    # probed, so Mine must come from `bd list --assignee`).
    mine = create("claimed row")
    p = b.raw_bd(store, "update", mine, "--claim", "--json",
                 actor_name="lab-hci")
    assert p.returncode == 0, p.stderr
    ids["mine"] = mine

    # S6 conflict target (description edit lands here externally).
    ids["conflict"] = create("conflict edit target", description="orig")

    # S5 dead worker (claimed by lab-dead inside the scenario).
    ids["dead"] = create("dead worker task")

    # S10: P10 blocked directly by g1+g2; x10 child of P10 (inherited) AND
    # directly blocked by g3 => multi-blocker diagnosis.
    p10 = create("inherit gate")
    g1, g2, g3 = (create("multi blocker one"), create("multi blocker two"),
                  create("direct blocker"))
    for g in (g1, g2):
        assert b.raw_bd(store, "dep", "add", p10, g, "--json",
                        actor_name="lab-seeder").returncode == 0
    x10 = create("inherited victim")
    parent(x10, p10)
    assert b.raw_bd(store, "dep", "add", x10, g3, "--json",
                    actor_name="lab-seeder").returncode == 0
    ids.update(p10=p10, g1=g1, g2=g2, g3=g3, x10=x10)

    # S7 churn fixtures: a row to reparent and its two candidate parents.
    ids["moveHostA"] = create("move host A")
    ids["moveHostB"] = create("move host B")
    mv = create("move me")
    parent(mv, ids["moveHostA"])
    ids["moveMe"] = mv

    store_info = {"workspace": store, "db": store_db(store)}
    return store, ids, store_info


def store_db(store):
    p = b.raw_bd(store, "info", "--json", readonly=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["database_path"]


# fixed read argv table — every entry goes through raw_bd(readonly=True)
READS = {
    "ready":        lambda s, a: ["ready", "--json", "-n", "100",
                                  "--exclude-type=epic"],
    "ready_plain":  lambda s, a: ["ready", "--json", "-n", "100"],
    "mine":         lambda s, a: ["list", "--assignee", a[0], "--json"],
    "blocked":      lambda s, a: ["blocked", "--json"],
    "list_all":     lambda s, a: ["list", "--all", "--limit", "150",
                                  "--json"],
    "show":         lambda s, a: ["show", a[0], "--json"],
    "history":      lambda s, a: ["history", a[0], "--json"],
    "comments":     lambda s, a: ["comments", a[0], "--json"],
    "search":       lambda s, a: ["search", a[0], "--json"],
    "deptree":      lambda s, a: ["dep", "tree", a[0], "--json"],
    "info":         lambda s, a: ["info", "--json"],
    "epic_status":  lambda s, a: ["epic", "status", "--json"],
    "context":      lambda s, a: ["context"],
    "ready_explain": lambda s, a: ["ready", "--explain"],
}


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "seed":
        prefix = "scn"
        if "--prefix" in argv:
            prefix = argv[argv.index("--prefix") + 1]
        store, ids, store_info = seed_world(prefix)
        print(json.dumps({"store": store, "storeInfo": store_info,
                          "ids": ids}))
        return 0
    if cmd == "read":
        store, name = argv[1], argv[2]
        args = argv[3:]
        if name not in READS:
            print(json.dumps({"error": f"unknown read: {name}"}))
            return 2
        full = READS[name](store, args)
        # the provider argv form `blocked --limit N --json` (tree.mjs
        # refreshOnce shape) normalizes to the supported native argv
        if name == "blocked" and "--limit" in args:
            full = ["blocked", "--json"]
        p = b.raw_bd(store, *full, readonly=True)
        if p.returncode != 0:
            # honest: caller asserts on rc; show-of-deleted is a RESULT
            try:
                parsed = json.loads(p.stdout)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            print(json.dumps({"rc": p.returncode, "payload": parsed,
                              "stdout": p.stdout, "stderr": p.stderr}))
            return 0
        print(json.dumps({"rc": 0, "payload": json.loads(p.stdout)}))
        return 0
    if cmd == "act":
        store, actor_name = argv[1], argv[2]
        p = b.raw_bd(store, *argv[3:], actor_name=actor_name)
        print(json.dumps({"rc": p.returncode, "stdout": p.stdout,
                          "stderr": p.stderr}))
        return 0
    if cmd == "cleanup":
        shutil.rmtree(argv[1], ignore_errors=True)
        print(json.dumps({"removed": argv[1]}))
        return 0
    print(json.dumps({"error": f"unknown command: {cmd}"}))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
