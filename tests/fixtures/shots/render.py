# tests/fixtures/shots/render.py — drive the cloud browser to screenshot the
# pane over the captured world.json. Use inside the browser_exec tool:
#   exec(open("<repo>/tests/fixtures/shots/render.py").read()); shot("tree")
# Screenshots land in tests/fixtures/shots/out/ (gitignored).
import json, time, shutil, os
# __file__ is the harness's own module under exec() — callers may pin
# SHOTS_OUT before exec; default assumes the repo's absolute checkout path.
OUT = globals().get("SHOTS_OUT") or "/home/hermes/.hermes/work/beads-lab/v011-polish/tests/fixtures/shots/out"

def render(state):
    src = open(os.path.join(OUT, "app.js")).read()
    goto_url("about:blank"); wait_for_load()
    js("(() => { document.open(); document.write('<!doctype html><html lang=en><head><meta charset=utf-8><title>workbench</title></head><body><div id=root></div></body></html>'); document.close(); window.__SRC=[]; window.__WB_STATE=" + json.dumps(state) + "; return 1; })()")
    for i in range(0, len(src), 16000):
        js("(() => { window.__SRC.push(" + json.dumps(src[i:i+16000]) + "); return 1; })()")
    js("(() => { const s=document.createElement('script'); s.textContent=window.__SRC.join(''); document.body.appendChild(s); delete window.__SRC; return 1; })()")
    time.sleep(0.5)
    return js("JSON.stringify({items: document.querySelectorAll('[role=treeitem]').length, danger: document.getElementById('root').querySelectorAll('script,img').length})")

def shot(state, tries=3):
    last = None
    for _ in range(tries):
        try:
            p = capture_screenshot()
            dest = os.path.join(OUT, f"{state}.png")
            shutil.copy(p, dest)
            return dest
        except Exception as e:
            last = e
            time.sleep(1)
    raise last
