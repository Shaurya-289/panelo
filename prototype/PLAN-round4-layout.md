# Plan: Prototype round 4 (rearrangeable overlay panels, Windows-snap style)

## Context
`prototype/` is a Qt Quick (PySide6 6.11) prototype of the app's new UI with fake data. Read
`PLAN-overlay.md`, `PLAN-round2.md` and `PLAN-round3.md` first, for conventions and gotchas, then the
current code. **Only modify files inside `prototype/`; never touch `src/` or `tests/`.**

Today the overlay's five panels (Notes, To-Do, Files, Timers, Reminders) sit in a **fixed** layout in
`qml/Overlay.qml`:
- tall left column
- middle column split into To-Do and Files
- right column split into Timers and Reminders
- four `Splitter`s; fractions saved in `Settings { category: "overlay" }` (`colLeft`, `colMid`, `midSplit`,
  `rightSplit`)

The user wants the arrangement to be **free and interchangeable, like window snapping in Windows**. All
decisions below are theirs; implement exactly.

## User decisions
- **Overlay only.** The Full App layout does not change.
- **All 5 panels are always visible.** No hiding, no maximizing a single panel.
- **Moving a panel = drag its header.**
  - Press on the header and move past the drag threshold: the panel **lifts** (scale about 0.96,
    opacity about 0.85, soft shadow) and follows the cursor.
  - Its old spot shows a **faint empty outline** until the drop.
  - A quick **double-click still morphs to the Full App** (keep `expandRequested`). A plain click on
    the header does nothing.
  - **Esc** during a drag cancels it and glides the panel back.
- **Dropping onto another panel depends on where:**
  - **middle** zone → the two panels **swap** places (and sizes)
  - **edge** zone (left/right/top/bottom band, about 28% of that panel's size, at least 48px) →
    **split** that panel's space and sit beside it on that side, 50/50
  - a **snap preview** shows the exact rectangle(s) the panels will occupy: a translucent white
    rounded rect with a 1.5px white border, plus a smaller outline for where the target panel will
    end up when splitting
  - dropping anywhere that is not a valid target (or invalid because of minimum sizes) cancels
    and glides back
- **Layout picker, in both places:**
  1. **A button next to the search bar** (a `⊞` layout glyph `IconButton`) opens a glass popup. It
     shows the 4 template thumbnails, highlights the active shape, and has **"Reset to default"**.
     Clicking a template applies it.
  2. **While dragging a panel**, the same thumbnails slide down at the **top of the screen** (above
     the search bar's area, Windows 11 snap-layout style). Hovering a thumbnail highlights its slots.
     Dropping the dragged panel **onto a slot** applies that template with the dragged panel in that
     slot; the other panels fill the remaining slots.
- **Templates** (slot numbers 1–5 = slot order by area, largest first):
  1. **Your sketch (default):** tall left | middle stacked 2 | right stacked 2. Default panel
     assignment: Notes left, To-Do top-middle, Files bottom-middle, Timers top-right, Reminders
     bottom-right, with the current default fractions.
  2. **One big focus:** one large panel on the left (about 60% width), and a 2×2 grid of four on the
     right.
  3. **Top row + bottom row:** two wide panels on top, three on the bottom (about 50/50 height).
  4. **Five columns:** all five side by side, full height, equal widths.
- **When applying a template, keep the current order.** Rank the panels by their current area,
  largest first. Panel *k* in that ranking goes into slot *k*. Ties keep the order Notes, To-Do,
  Files, Timers, Reminders. (When dropping onto a slot from the drag picker, the dragged panel takes
  that slot and the rest follow the same ranking.)
- **Minimum size:** each panel is at least **220×160 px** (the content must stay readable).
  - Splitter drags clamp so no panel goes below it.
  - A split drop that would make any panel smaller shows the preview in **muted red**
    (`Theme.danger` at low alpha) and is refused on drop.
  - A template that can't fit (e.g. five columns on a narrow screen) is greyed out in the picker.
- **Motion:** every layout change (swap, split, template, reset, cancelled drop) **glides** the
  panels to their new rectangles in about 220 ms (`Theme.easing`). The panels are persistent items;
  only their x/y/width/height animate. Splitter drags resize live, with no animation.
- **Persistence:** only the **current layout** is remembered, across overlay opens and app restarts.
  There are no named layouts. "Reset to default" returns to the sketch template with the default
  assignment.

## Implementation design
**Layout model: a tree, saved as JSON in `Settings`** (e.g. `property string layoutJson`, replacing
the four fraction properties; migrate silently to the default).
```js
// container
{ "dir": "row" | "col", "sizes": [0.3, 0.4, 0.3], "children": [ node, node, node ] }
// leaf
{ "panel": "notes" | "todo" | "files" | "timers" | "reminders" }
```
The sizes are fractions of the container's main axis and sum to 1. The sketch template is:
```js
{dir:"row", sizes:[0.30,0.40,0.30], children:[
  {panel:"notes"},
  {dir:"col", sizes:[0.38,0.62], children:[{panel:"todo"},{panel:"files"}]},
  {dir:"col", sizes:[0.50,0.50], children:[{panel:"timers"},{panel:"reminders"}]}]}
```
Put the pure tree logic in a JS library, `qml/Proto/layout.js` (`.pragma library`), so it is easy
to unit-test from the selftest via the QML engine:
- `computeRects(tree, rect, gap)` → `{ panelKey: rect }` plus a list of splitter descriptors
  `{ path, index, dir, rect }` (the gap between child *i* and *i+1* of the container at `path`).
- `swap(tree, a, b)`, `remove(tree, panel)` (collapses single-child containers into their parent;
  merges a child container that has the same `dir` as its parent), and
  `splitAt(tree, target, panel, side)` (`side` ∈ left/right/top/bottom: remove `panel`, then
  replace the `target` leaf with a container of the matching `dir`, sizes `[0.5, 0.5]`, ordered by
  side; if the target's parent already has that `dir`, insert as a sibling and split the target's
  share in half instead of nesting).
- `applyTemplate(name, order)`, where `order` is the panel keys by rank, and
  `templateSlotRects(name, rect, gap)` for thumbnails and slot hit-testing.
- `rankByArea(rects)`, `fits(tree, rect, gap, minW, minH)` and `validate(tree)` (exactly the 5 panels,
  each once; the sizes are valid). An invalid saved JSON falls back to the default.
- `resize(tree, path, index, deltaFraction, rect, gap, minW, minH)`: move the splitter between child
  `index` and `index+1`, clamped so every panel in both subtrees stays at or above the minimum.

**Overlay.qml:**
- Remove the hard-coded `x/y/width/height` bindings and the 4 fixed `Splitter`s.
- Each panel's geometry binds to `overlay.rects[key]`, recomputed from the tree and the group rect.
  Add `Behavior`s on x/y/width/height that are disabled while a splitter is being dragged (a
  `resizing` flag) and during the morph.
- Splitters: a `Repeater` over the splitter descriptors, using the existing `Splitter` component,
  that calls `layout.resize` on drag.
- The morph and pre-warm logic from round 3 must keep working with the new geometry (the panel group
  is still one item that gets cached and scaled).

**`OverlayPanel.qml`:**
- Add a header `DragHandler` (`target: null`, threshold = the platform default) that emits
  `dragStarted`, `dragMoved(scenePos)` and `dragEnded(scenePos)`.
- Keep the double-tap `TapHandler` for `expandRequested`.
- The header cursor is `Qt.OpenHandCursor` on hover and `Qt.ClosedHandCursor` while dragging.
- Keep the round-3 click-through rules: the header must not leak clicks to panels beneath.

**A drag controller in Overlay.qml:**
- It tracks the dragged panel and the hit-tests the target panel and zone (center/edge) under the
  cursor from the current rects.
- It computes the preview tree via `swap`/`splitAt` and shows the preview rects. While dragging it
  shows the **top snap bar** (`LayoutPicker` in compact horizontal form, sliding down from y=-80).
- On drop: commit the tree (and save it), or cancel. The lifted panel is rendered on top (higher `z`)
  during the drag.

**New components:**
- `qml/Proto/LayoutThumb.qml`: a mini rendering of a template's slots (rounded rects, gap 3px), with
  hover-highlighted slots, the active state, and a disabled state.
- `qml/Proto/LayoutPicker.qml`: the popup (thumbnails grid plus "Reset to default", a glass popup
  with `ClickBlocker` outside, like `TimePickerPopup`/`ContextMenu`) and the drag-time top bar form.
- Add both to `qmldir`.

## Verification (do all of it, and report honestly)
1. `QT_FORCE_STDERR_LOGGING=1`, `timeout 60`: **zero QML warnings or errors**.
2. **`--selftest`:** all the existing 76 checks still pass. Add checks:
   - **`layout.js` unit checks** (call them through a small QML helper or `QJSEngine` loading the
     library):
     - default tree validity and its rects match the old fixed layout within 1px
     - swap
     - splitAt for each side, including the same-dir sibling insert
     - remove collapses single children
     - each template has 5 slots, and applyTemplate keeps the ranking
     - resize clamps at the minimum size
     - `fits` is false for five columns at a 900px width
     - an invalid JSON falls back to default
   - **UI checks with real QTest mouse events:**
     - Dragging Notes' header onto the middle of Timers swaps them (check `overlay.rects`).
     - Dragging To-Do onto the right edge of Files splits it (To-Do now right of Files, same row).
     - A drop that violates the minimum size is refused, and the layout is unchanged.
     - Esc mid-drag cancels.
     - A double-click on a header still morphs.
     - The picker button opens the popup, and clicking "Five columns" applies it (5 equal widths).
     - During a drag the top bar appears; dropping on slot 1 of "One big focus" puts the dragged
       panel in the big slot.
     - "Reset to default" restores the sketch.
     - The layout persists: re-load it from the settings file and assert the same tree.
     - A splitter drag still resizes, and clamps at the minimum.
3. **Screenshots** in `prototype/.shots/` with `--fake-backdrop` and a temp `--library`:
   - `layout-picker` (popup open)
   - `layout-drag-swap` (mid-drag over a panel's center, preview showing)
   - `layout-drag-split` (over an edge, preview showing)
   - `layout-drag-invalid` (red preview)
   - `layout-topbar` (dragging, top bar showing)
   - `layout-big` (One big focus applied)
   - `layout-rows`
   - `layout-columns`
   - `overlay` (default)

   View each one with Read and fix any visual problems: panel content must look fine at every size
   at or above the minimum (no overlaps or clipped headers).
4. `ruff check prototype` is clean. Leave no processes running.
