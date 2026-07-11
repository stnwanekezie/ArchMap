"""Record demo GIFs of the archmap viewer.

Drives the built self-contained HTML through its own in-page JS API — no mouse
simulation, so the result is deterministic — and encodes each interaction into
an animated GIF under this ``docs/`` folder.

This is a **dev-only asset generator**; archmap's runtime stays zero-dependency::

    pip install playwright pillow
    python -m playwright install chromium
    # build the map first (from backend/):  python -m tools.archmap
    python tools/archmap/docs/record_demos.py

Flags: ``--html`` (built HTML to record, default ``../backend_archmap.html``),
``--out-dir`` (default this folder), ``--only search,explore,filter``.
"""

from __future__ import annotations

import argparse
import io
import os
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright

VIEW_W, VIEW_H = 1080, 640

# Injected once per page: place a world point at the canvas centre at a given
# zoom, and look a node up by id. Kept tiny; everything else reuses the viewer's
# own globals (nodes, byId, view, draw, fit, selectNode, runSearch, ...).
_HELPERS = """
window.__setView = (scale, cx, cy) => {
  view.scale = scale;
  view.x = cv.clientWidth / 2 - cx * scale;
  view.y = cv.clientHeight / 2 - cy * scale;
  draw();
};
window.__node = (id) => { const n = byId.get(id); return n ? {x: n.x, y: n.y} : null; };
window.__topClass = () => {
  let best = null, score = -1;
  for (const n of nodes) {
    if (n.kind !== 'class') continue;
    const s = (outCalls.get(n.id) || []).length
            + (inCalls.get(n.id) || []).length
            + (children.get(n.id) || []).length;
    if (s > score) { score = s; best = n; }
  }
  return best ? best.id : null;
};
window.__neighbor = (id) => {
  const kids = children.get(id) || [], calls = outCalls.get(id) || [];
  return (kids[0] || calls[0] || null);
};
"""


def grab(page: Page) -> Image.Image:
    """Capture the current viewport as an RGB frame."""
    return Image.open(io.BytesIO(page.screenshot(type="png"))).convert("RGB")


def hold(frames: list[Image.Image], durations: list[int], ms: int) -> None:
    """Extend the last frame's on-screen time by ``ms`` (a pause, not a copy)."""
    if frames:
        durations[-1] += ms


def save_gif(frames: list[Image.Image], durations: list[int], path: Path) -> None:
    """Encode frames to a looping GIF with a per-frame adaptive palette."""
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=128) for f in frames]
    pal[0].save(path, save_all=True, append_images=pal[1:], duration=durations,
                loop=0, disposal=2, optimize=True)
    kb = os.path.getsize(path) // 1024
    print(f"  wrote {path.name}  ({kb} KB, {len(frames)} frames)")


def _tween_to(page: Page, frames, durations, cx, cy, scale, *, steps=9, ms=45):
    """Animate the view from its current pose to (cx, cy)@scale, capturing each step."""
    cur = page.evaluate("({s: view.scale, x: view.x, y: view.y, "
                        "w: cv.clientWidth, h: cv.clientHeight})")
    # Recover the current centre world point from the live view transform.
    cx0 = (cur["w"] / 2 - cur["x"]) / cur["s"]
    cy0 = (cur["h"] / 2 - cur["y"]) / cur["s"]
    s0 = cur["s"]
    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)  # smoothstep
        page.evaluate("([s, x, y]) => window.__setView(s, x, y)",
                      [s0 + (scale - s0) * ease,
                       cx0 + (cx - cx0) * ease,
                       cy0 + (cy - cy0) * ease])
        frames.append(grab(page))
        durations.append(ms)


def demo_search(page: Page):
    """Type a query so matches light up + get counted, then frame them."""
    frames: list[Image.Image] = []
    durations: list[int] = []
    page.evaluate("() => { search.value = ''; runSearch(); fit(); }")
    frames.append(grab(page)); durations.append(700)  # overview

    word = "buffer"
    for i in range(1, len(word) + 1):
        page.evaluate("(q) => { search.value = q; runSearch(); }", word[:i])
        frames.append(grab(page)); durations.append(190)
    hold(frames, durations, 900)                       # read the glowing matches

    page.evaluate("() => fitToMatches()")              # the Enter-to-frame gesture
    frames.append(grab(page)); durations.append(1500)
    hold(frames, durations, 1200)
    return frames, durations


def demo_explore(page: Page):
    """Open a node's side panel, then jump to a neighbour."""
    frames: list[Image.Image] = []
    durations: list[int] = []
    page.evaluate("() => { search.value = ''; runSearch(); fit(); }")
    frames.append(grab(page)); durations.append(700)

    nid = page.evaluate("() => window.__topClass()")
    if not nid:
        nid = page.evaluate("() => nodes[0].id")
    pos = page.evaluate("(id) => window.__node(id)", nid)
    _tween_to(page, frames, durations, pos["x"], pos["y"], 1.4)
    page.evaluate("(id) => selectNode(byId.get(id))", nid)   # opens the panel
    frames.append(grab(page)); durations.append(400)
    hold(frames, durations, 1600)                            # read the panel

    neigh = page.evaluate("(id) => window.__neighbor(id)", nid)
    if neigh:
        npos = page.evaluate("(id) => window.__node(id)", neigh)
        page.evaluate("(id) => selectNode(byId.get(id))", neigh)  # jump
        _tween_to(page, frames, durations, npos["x"], npos["y"], 1.6, steps=7)
        hold(frames, durations, 1800)
    return frames, durations


def demo_filter(page: Page):
    """Toggle relation pills and layers to declutter the graph."""
    frames: list[Image.Image] = []
    durations: list[int] = []
    page.evaluate("() => { search.value = ''; runSearch(); fit(); }")
    frames.append(grab(page)); durations.append(900)

    # Turn off 'calls' edges, then hide the two densest layers, one at a time.
    page.locator("#toggle-calls").click()
    frames.append(grab(page)); durations.append(900)

    for label in ("adapters", "gtfs"):
        row = page.locator("#legend .row", has_text=label).first
        if row.count():
            row.click()
            frames.append(grab(page)); durations.append(900)
    hold(frames, durations, 1400)
    return frames, durations


DEMOS = {"search": demo_search, "explore": demo_explore, "filter": demo_filter}


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Record archmap demo GIFs.")
    ap.add_argument("--html", default=str(here.parent / "backend_archmap.html"),
                    help="built archmap HTML to record")
    ap.add_argument("--out-dir", default=str(here), help="where to write the GIFs")
    ap.add_argument("--only", default="", help="comma-separated subset of: "
                    + ", ".join(DEMOS))
    args = ap.parse_args()

    html = Path(args.html).resolve()
    if not html.exists():
        raise SystemExit(f"built map not found: {html}\n"
                         "build it first:  python -m tools.archmap")
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = [d.strip() for d in args.only.split(",") if d.strip()] or list(DEMOS)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": VIEW_W, "height": VIEW_H},
                                device_scale_factor=1)
        page.add_init_script(_HELPERS)      # inject helpers on every load
        for name in wanted:
            print(f"[demo] {name}")
            # Fresh load per demo so panel/filter/search state never leaks across.
            page.goto(html.as_uri())
            page.wait_for_function("typeof nodes !== 'undefined' && nodes.length > 0")
            page.wait_for_timeout(500)      # let the initial fit() settle
            frames, durations = DEMOS[name](page)
            save_gif(frames, durations, out_dir / f"{name}.gif")
        browser.close()


if __name__ == "__main__":
    main()
