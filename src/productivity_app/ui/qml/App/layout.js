.pragma library

// Pure layout-tree logic for the overlay's five panels (no QML types in here, so the
// self-test can call it directly).
//
// A layout is a tree:
//   container: { dir: "row" | "col", sizes: [fractions summing to 1], children: [node, ...] }
//   leaf:      { panel: "notes" | "todo" | "files" | "timers" | "reminders" }
// `sizes` are fractions of the container's main axis *after* the gaps are taken out.
// Rectangles are plain objects { x, y, width, height }. Every function that changes a tree
// returns a new one; the input is never modified.

var PANELS = ["notes", "todo", "files", "timers", "reminders"]
var TEMPLATES = ["sketch", "focus", "rows", "columns"]
var MIN_W = 220
var MIN_H = 160
var SIDES = ["left", "right", "top", "bottom"]

function clone(value) { return JSON.parse(JSON.stringify(value)) }
function isLeaf(node) { return node !== null && typeof node === "object" && typeof node.panel === "string" }
function leaf(panel) { return { panel: panel } }
function box(dir, sizes, children) { return { dir: dir, sizes: sizes, children: children } }
function rectOf(r) { return { x: r.x, y: r.y, width: r.width, height: r.height } }

// ── Trees ───────────────────────────────────────────────────────────

// The sketch: tall Notes on the left, To-Do over Files in the middle, Timers over Reminders on the right.
function defaultTree() {
    return box("row", [0.30, 0.40, 0.30], [
        leaf("notes"),
        box("col", [0.38, 0.62], [leaf("todo"), leaf("files")]),
        box("col", [0.50, 0.50], [leaf("timers"), leaf("reminders")])
    ])
}

// Collapses single-child containers and merges a child container into its parent when both
// run the same way. Sizes are renormalised to sum to 1.
function normalize(node) {
    if (isLeaf(node))
        return node
    var kids = [], sizes = []
    for (var i = 0; i < node.children.length; i++) {
        var child = normalize(node.children[i])
        if (child === null)
            continue
        if (!isLeaf(child) && child.dir === node.dir) {
            for (var j = 0; j < child.children.length; j++) {
                kids.push(child.children[j])
                sizes.push(node.sizes[i] * child.sizes[j])
            }
        } else {
            kids.push(child)
            sizes.push(node.sizes[i])
        }
    }
    if (kids.length === 0)
        return null
    if (kids.length === 1)
        return kids[0]
    var sum = 0
    for (var k = 0; k < sizes.length; k++)
        sum += sizes[k]
    for (var m = 0; m < sizes.length; m++)
        sizes[m] = sizes[m] / sum
    return { dir: node.dir, sizes: sizes, children: kids }
}

function leafKeys(node, out) {
    out = out || []
    if (isLeaf(node))
        out.push(node.panel)
    else
        for (var i = 0; i < node.children.length; i++)
            leafKeys(node.children[i], out)
    return out
}

// True for a tree with exactly the five panels, each once, and sane sizes.
function validate(tree) {
    function walk(node) {
        if (isLeaf(node))
            return PANELS.indexOf(node.panel) >= 0
        if (node === null || typeof node !== "object" || (node.dir !== "row" && node.dir !== "col"))
            return false
        if (!Array.isArray(node.children) || !Array.isArray(node.sizes)
                || node.children.length < 2 || node.children.length !== node.sizes.length)
            return false
        var sum = 0
        for (var i = 0; i < node.sizes.length; i++) {
            var s = node.sizes[i]
            if (typeof s !== "number" || !isFinite(s) || s <= 0.001)
                return false
            sum += s
        }
        if (Math.abs(sum - 1) > 0.001)
            return false
        for (var j = 0; j < node.children.length; j++)
            if (!walk(node.children[j]))
                return false
        return true
    }
    if (!walk(tree))
        return false
    var keys = leafKeys(tree).sort()
    return keys.length === PANELS.length && keys.join(",") === PANELS.slice().sort().join(",")
}

// Saved JSON -> tree. Anything unreadable or invalid falls back to the default layout.
function parse(json) {
    try {
        var value = JSON.parse(json)
        if (validate(value))
            return value
    } catch (e) {
    }
    return defaultTree()
}

function stringify(tree) { return JSON.stringify(tree) }

// A compact description of a tree's shape (ignores panels and sizes): "R(L,C(L,L),C(L,L))".
function shapeOf(node) {
    if (isLeaf(node))
        return "L"
    var parts = []
    for (var i = 0; i < node.children.length; i++)
        parts.push(shapeOf(node.children[i]))
    return (node.dir === "row" ? "R" : "C") + "(" + parts.join(",") + ")"
}

// ── Geometry ────────────────────────────────────────────────────────

// Where everything goes inside `rect`, with `gap` between neighbours. Returns
//   rects:     { panelKey: rect }
//   splitters: [{ path, index, dir, rect, avail }] one per gap between child `index` and
//              `index + 1` of the container at `path` (a list of child indices from the root);
//              `avail` is that container's main-axis size without gaps.
function computeRects(tree, rect, gap) {
    var rects = {}, splitters = []
    place(tree, rectOf(rect), [], rects, splitters, gap)
    return { rects: rects, splitters: splitters }
}

function childRects(node, r, gap) {
    var row = node.dir === "row"
    var n = node.children.length
    var avail = Math.max(1, (row ? r.width : r.height) - gap * (n - 1))
    var out = [], pos = row ? r.x : r.y
    for (var i = 0; i < n; i++) {
        var size = avail * node.sizes[i]
        out.push(row ? { x: pos, y: r.y, width: size, height: r.height }
                     : { x: r.x, y: pos, width: r.width, height: size })
        pos += size + gap
    }
    return { rects: out, avail: avail }
}

function place(node, r, path, rects, splitters, gap) {
    if (isLeaf(node)) {
        rects[node.panel] = r
        return
    }
    var cr = childRects(node, r, gap)
    var row = node.dir === "row"
    for (var i = 0; i < node.children.length; i++) {
        place(node.children[i], cr.rects[i], path.concat([i]), rects, splitters, gap)
        if (i < node.children.length - 1) {
            var c = cr.rects[i]
            splitters.push({
                path: path, index: i, dir: node.dir, avail: cr.avail,
                rect: row ? { x: c.x + c.width, y: r.y, width: gap, height: r.height }
                          : { x: r.x, y: c.y + c.height, width: r.width, height: gap }
            })
        }
    }
}

// The node at `path` and the rectangle it occupies.
function locate(tree, path, rect, gap) {
    var node = tree, r = rectOf(rect)
    for (var i = 0; i < path.length; i++) {
        r = childRects(node, r, gap).rects[path[i]]
        node = node.children[path[i]]
    }
    return { node: node, rect: r }
}

// Smallest size a subtree needs along an axis (x: true for width).
function minSize(node, alongX, gap, minW, minH) {
    if (isLeaf(node))
        return alongX ? minW : minH
    var sum = 0, widest = 0
    for (var i = 0; i < node.children.length; i++) {
        var s = minSize(node.children[i], alongX, gap, minW, minH)
        sum += s
        widest = Math.max(widest, s)
    }
    return (node.dir === "row") === alongX ? sum + gap * (node.children.length - 1) : widest
}

// Every panel at least minW x minH (half a pixel of slack for rounding)?
function fits(tree, rect, gap, minW, minH) {
    var rects = computeRects(tree, rect, gap).rects
    for (var key in rects)
        if (rects[key].width < minW - 0.5 || rects[key].height < minH - 0.5)
            return false
    return true
}

// Moves the splitter between child `index` and `index + 1` of the container at `path` by
// `deltaFraction` of that container's main axis, so that neither side drops below the minimum.
// If the splitter is already beyond a limit, it is never pushed against the drag direction.
function resize(tree, path, index, deltaFraction, rect, gap, minW, minH) {
    var t = clone(tree)
    var info = locate(t, path, rect, gap)
    var node = info.node
    var row = node.dir === "row"
    var n = node.children.length
    var avail = Math.max(1, (row ? info.rect.width : info.rect.height) - gap * (n - 1))
    var a = node.sizes[index], b = node.sizes[index + 1], pair = a + b
    var lo = minSize(node.children[index], row, gap, minW, minH) / avail
    var hi = pair - minSize(node.children[index + 1], row, gap, minW, minH) / avail
    if (lo > hi)
        return t
    lo = Math.min(lo, a)
    hi = Math.max(hi, a)
    var next = Math.max(lo, Math.min(hi, a + deltaFraction))
    node.sizes[index] = next
    node.sizes[index + 1] = pair - next
    return t
}

// ── Edits ───────────────────────────────────────────────────────────

function swap(tree, a, b) {
    var t = clone(tree)
    ;(function walk(node) {
        if (isLeaf(node)) {
            if (node.panel === a) node.panel = b
            else if (node.panel === b) node.panel = a
        } else {
            for (var i = 0; i < node.children.length; i++)
                walk(node.children[i])
        }
    })(t)
    return t
}

function removeNode(node, panel) {
    if (isLeaf(node))
        return node.panel === panel ? null : node
    var kids = [], sizes = []
    for (var i = 0; i < node.children.length; i++) {
        var kept = removeNode(node.children[i], panel)
        if (kept !== null) {
            kids.push(kept)
            sizes.push(node.sizes[i])
        }
    }
    return kids.length === 0 ? null : { dir: node.dir, sizes: sizes, children: kids }
}

// Takes `panel` out. Containers left with one child collapse into their parent, and a child
// container running the same way as its parent is merged into it.
function remove(tree, panel) {
    return normalize(removeNode(clone(tree), panel))
}

function findLeaf(node, panel, parent, index) {
    if (isLeaf(node))
        return node.panel === panel ? { parent: parent, index: index } : null
    for (var i = 0; i < node.children.length; i++) {
        var found = findLeaf(node.children[i], panel, node, i)
        if (found !== null)
            return found
    }
    return null
}

// Moves `panel` next to `target`, on `side` of it, splitting target's space 50/50. When target's
// parent already runs that way the panel becomes a sibling (target's share is halved) instead of
// nesting. Returns null when the move makes no sense.
function splitAt(tree, target, panel, side) {
    if (target === panel || SIDES.indexOf(side) < 0)
        return null
    var t = remove(tree, panel)
    if (t === null)
        return null
    var found = findLeaf(t, target, null, -1)
    if (found === null)
        return null
    var dir = (side === "left" || side === "right") ? "row" : "col"
    var before = side === "left" || side === "top"
    var pair = before ? [leaf(panel), leaf(target)] : [leaf(target), leaf(panel)]
    if (found.parent === null)
        return box(dir, [0.5, 0.5], pair)
    var p = found.parent, i = found.index
    if (p.dir === dir) {
        var share = p.sizes[i]
        p.children.splice(i, 1, pair[0], pair[1])
        p.sizes.splice(i, 1, share / 2, share / 2)
    } else {
        p.children[i] = box(dir, [0.5, 0.5], pair)
    }
    return normalize(t)
}

// ── Templates ───────────────────────────────────────────────────────
// Slot numbers 1..5 go by area, largest first (ties: reading order).

function templateTree(name, order) {
    function s(k) { return leaf(order[k - 1]) }
    var third = 1 / 3
    switch (name) {
    case "focus":     // one big panel, a 2x2 grid on the right
        return box("row", [0.60, 0.40], [s(1), box("col", [0.5, 0.5], [
            box("row", [0.5, 0.5], [s(2), s(3)]), box("row", [0.5, 0.5], [s(4), s(5)])])])
    case "rows":      // two wide on top, three below
        return box("col", [0.5, 0.5], [
            box("row", [0.5, 0.5], [s(1), s(2)]), box("row", [third, third, third], [s(3), s(4), s(5)])])
    case "columns":   // five equal columns
        return box("row", [0.2, 0.2, 0.2, 0.2, 0.2], [s(1), s(2), s(3), s(4), s(5)])
    default:          // "sketch"
        return box("row", [0.30, 0.40, 0.30], [
            s(1), box("col", [0.38, 0.62], [s(3), s(2)]), box("col", [0.50, 0.50], [s(4), s(5)])])
    }
}

// `order[k - 1]` is the panel that goes into slot k.
function applyTemplate(name, order) { return templateTree(name, order) }

// The five slot rectangles of a template inside `rect` (index = slot number - 1).
function templateSlotRects(name, rect, gap) {
    var rects = computeRects(templateTree(name, ["1", "2", "3", "4", "5"]), rect, gap).rects
    return [rects["1"], rects["2"], rects["3"], rects["4"], rects["5"]]
}

// Which template (if any) has the same shape as `tree`.
function activeTemplate(tree) {
    var shape = shapeOf(tree)
    for (var i = 0; i < TEMPLATES.length; i++)
        if (shapeOf(templateTree(TEMPLATES[i], ["1", "2", "3", "4", "5"])) === shape)
            return TEMPLATES[i]
    return ""
}

// Panels by current area, largest first. Ties keep the order Notes, To-Do, Files, Timers, Reminders.
function rankByArea(rects) {
    var items = PANELS.map(function (key, i) {
        var r = rects[key]
        return { key: key, area: r ? r.width * r.height : 0, i: i }
    })
    items.sort(function (a, b) {
        var big = Math.max(a.area, b.area)
        if (Math.abs(a.area - b.area) > big * 1e-6)
            return b.area - a.area
        return a.i - b.i
    })
    return items.map(function (it) { return it.key })
}

// The slot order for dropping `panel` on `slot` (1-based): the panel takes that slot, the others
// fill the remaining slots by rank.
function orderForSlot(rank, panel, slot) {
    var rest = rank.filter(function (p) { return p !== panel })
    rest.splice(Math.max(0, Math.min(rest.length, slot - 1)), 0, panel)
    return rest
}

// ── Hit testing for drag and drop ───────────────────────────────────

// Which panel is under (x, y), ignoring `exclude`. "" when over a gap or nothing.
function panelAt(rects, x, y, exclude) {
    for (var key in rects) {
        var r = rects[key]
        if (key !== exclude && x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height)
            return key
    }
    return ""
}

// The drop zone of a panel's rectangle: "center", or the nearest edge band (28% of the size,
// at least 48px) as "left" | "right" | "top" | "bottom".
function zoneAt(r, x, y) {
    var bx = Math.min(Math.max(48, r.width * 0.28), r.width * 0.45)
    var by = Math.min(Math.max(48, r.height * 0.28), r.height * 0.45)
    var best = "center", score = 1
    var tests = [["left", (x - r.x) / bx], ["right", (r.x + r.width - x) / bx],
                 ["top", (y - r.y) / by], ["bottom", (r.y + r.height - y) / by]]
    for (var i = 0; i < tests.length; i++) {
        if (tests[i][1] < score) {
            score = tests[i][1]
            best = tests[i][0]
        }
    }
    return best
}
