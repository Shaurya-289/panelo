from productivity_app.desktop.ui.workspace_geometry import (
    resize_sweep,
    snap,
    snap_resize,
    swapped,
    valid,
)
from productivity_app.desktop.ui.workspace_state import Rect, default_layout


def test_default_swap_collision_and_sweep():
    layout = default_layout()
    assert valid(layout)
    assert valid(swapped(layout, "todo", "files"))
    assert swapped(layout, "notes", "timers") == layout
    assert not valid({**layout, "todo": Rect(0, 0, 361, 323)})
    r = resize_sweep(layout, "todo", Rect(521, 0, 650, 323))
    assert r.right == 888 and valid({**layout, "todo": r})
    r, _ = snap(layout, "todo", Rect(520, 0, 361, 323))
    assert r.x == 521


def test_resize_stops_at_collision_no_tunneling_no_neighbor_change():
    layout = default_layout()
    original = layout.copy()
    result = resize_sweep(layout, "todo", Rect(521, 0, 361, 650))
    assert result.bottom == layout["files"].y - 8
    assert layout == original
    result = resize_sweep(layout, "notes", Rect(0, 0, 1200, 660))
    assert result.right == layout["todo"].x - 8
    assert valid({**layout, "notes": result})


def test_snap_hysteresis_acquisition_release_and_resize():
    layout = default_layout()
    candidate, held = snap(layout, "notes", Rect(3, 0, 500, 660))
    assert candidate.x == 0 and held[0] == 0
    candidate, held = snap(layout, "notes", Rect(12, 0, 500, 660), held=held)
    assert candidate.x == 0
    candidate, held = snap(layout, "notes", Rect(15, 0, 495, 660), held=held)
    assert held[0] != 0
    candidate, held = snap_resize(layout, "notes", Rect(0, 0, 510, 660), "r", 1.0, (None, None))
    assert candidate.right == 513
    assert valid({**layout, "notes": candidate})
