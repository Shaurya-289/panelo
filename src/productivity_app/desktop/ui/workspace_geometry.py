"""Pure deterministic collision, sweep, snap and swap operations."""

from productivity_app.desktop.ui.workspace_state import MINIMA, PANEL_IDS, Rect


def valid(layout: dict[str, Rect], width: int = 1180, height: int = 660) -> bool:
    if (
        set(layout) != set(PANEL_IDS)
        or type(width) is not int
        or type(height) is not int
        or not (1 <= width <= 10000 and 1 <= height <= 10000)
    ):
        return False
    for key, r in layout.items():
        if any(type(n) is not int for n in (r.x, r.y, r.w, r.h)):
            return False
        mw, mh = MINIMA[key]
        if r.x < 0 or r.y < 0 or r.w < mw or r.h < mh or r.right > width or r.bottom > height:
            return False
    rows = list(layout.values())
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            if not (
                a.right + 8 <= b.x
                or b.right + 8 <= a.x
                or a.bottom + 8 <= b.y
                or b.bottom + 8 <= a.y
            ):
                return False
    return True


def swapped(layout: dict[str, Rect], first: str, second: str) -> dict[str, Rect]:
    result = dict(layout)
    result[first], result[second] = result[second], result[first]
    return result if valid(result) else dict(layout)


def resize_sweep(layout: dict[str, Rect], key: str, candidate: Rect) -> Rect:
    start = layout[key]
    steps = max(
        abs(a - b)
        for a, b in zip(
            (start.x, start.y, start.w, start.h),
            (candidate.x, candidate.y, candidate.w, candidate.h),
            strict=True,
        )
    )
    previous = start
    for i in range(1, min(steps, 10000) + 1):
        r = Rect(
            *(
                round(a + (b - a) * i / steps)
                for a, b in zip(
                    (start.x, start.y, start.w, start.h),
                    (candidate.x, candidate.y, candidate.w, candidate.h),
                    strict=True,
                )
            )
        )
        if not valid({**layout, key: r}):
            break
        previous = r
    return previous


def snap(
    layout: dict[str, Rect],
    key: str,
    r: Rect,
    scale: float = 1.0,
    held: tuple[int | None, int | None] = (None, None),
) -> tuple[Rect, tuple[int | None, int | None]]:
    targets_x: list[tuple[int, str, int]] = [(0, "", 0), (1180 - r.w, "", 1)]
    targets_y: list[tuple[int, str, int]] = [(0, "", 0), (660 - r.h, "", 1)]
    for other, b in layout.items():
        if other == key:
            continue
        targets_x.extend(
            (v, other, i) for i, v in enumerate((b.x, b.right - r.w, b.right + 8, b.x - r.w - 8))
        )
        targets_y.extend(
            (v, other, i) for i, v in enumerate((b.y, b.bottom - r.h, b.bottom + 8, b.y - r.h - 8))
        )

    def choose(value: int, targets: list[tuple[int, str, int]], old: int | None) -> int | None:
        if old is not None and abs(value - old) * scale <= 14:
            return old
        eligible = [t for t in targets if abs(value - t[0]) * scale <= 10]
        return (
            min(eligible, key=lambda t: (abs(value - t[0]), bool(t[1]), t[1], t[2]))[0]
            if eligible
            else None
        )

    x, y = choose(r.x, targets_x, held[0]), choose(r.y, targets_y, held[1])
    result = Rect(x if x is not None else r.x, y if y is not None else r.y, r.w, r.h)
    return (result, (x, y)) if valid({**layout, key: result}) else (r, (None, None))


def snap_resize(
    layout: dict[str, Rect],
    key: str,
    r: Rect,
    edges: str,
    scale: float,
    held: tuple[int | None, int | None],
) -> tuple[Rect, tuple[int | None, int | None]]:
    """Snap moving resize edges, without moving opposite edges or neighbors."""
    targets: list[list[tuple[int, str, int]]] = [
        [(0, "", 0), (1180, "", 1)],
        [(0, "", 0), (660, "", 1)],
    ]
    for identifier, sibling in layout.items():
        if identifier == key:
            continue
        targets[0].extend(
            (v, identifier, i)
            for i, v in enumerate((sibling.x, sibling.right, sibling.x - 8, sibling.right + 8))
        )
        targets[1].extend(
            (v, identifier, i)
            for i, v in enumerate((sibling.y, sibling.bottom, sibling.y - 8, sibling.bottom + 8))
        )
    values = (r.x if "l" in edges else r.right, r.y if "t" in edges else r.bottom)
    snapped: list[int | None] = [None, None]
    for axis, moving in enumerate((bool(set(edges) & {"l", "r"}), bool(set(edges) & {"t", "b"}))):
        if not moving:
            continue
        previous = held[axis]
        if previous is not None and abs(values[axis] - previous) * scale <= 14:
            snapped[axis] = previous
        else:
            eligible = [t for t in targets[axis] if abs(values[axis] - t[0]) * scale <= 10]
            if eligible:
                snapped[axis] = min(
                    eligible, key=lambda t: (abs(values[axis] - t[0]), bool(t[1]), t[1], t[2])
                )[0]
    x = snapped[0] if snapped[0] is not None else values[0]
    y = snapped[1] if snapped[1] is not None else values[1]
    candidate = Rect(
        x if "l" in edges else r.x,
        y if "t" in edges else r.y,
        r.right - x if "l" in edges else x - r.x if "r" in edges else r.w,
        r.bottom - y if "t" in edges else y - r.y if "b" in edges else r.h,
    )
    return (
        (candidate, (snapped[0], snapped[1]))
        if valid({**layout, key: candidate})
        else (r, (None, None))
    )
