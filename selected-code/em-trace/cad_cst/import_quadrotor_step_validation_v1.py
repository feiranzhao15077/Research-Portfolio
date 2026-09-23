"""Import the Stage 8A-3 AP214 quadrotor into a geometry-only CST project.

The script connects to the one running CST 2024 design environment, imports the
checked STEP file through a visible History entry, verifies the object tree and
save state, and intentionally leaves the saved project open.  It does not set
or start a solver and it does not configure RCS.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


# Configure this local CST library path before execution.
CST_PYTHON_LIB = None
REPO_ROOT = Path(__file__).resolve().parents[3]
STEP_PATH = (
    REPO_ROOT
    / "simulation"
    / "sw"
    / "exports"
    / "step"
    / "EM-Trace_quadrotor_full_v1_ap214.step"
)
PROJECT_PATH = (
    REPO_ROOT
    / "simulation"
    / "cst"
    / "work"
    / "quadrotor_step_import"
    / "EM-Trace_quadrotor_STEP_import_validation_v1.cst"
)
INVENTORY_PATH = PROJECT_PATH.parent / "CST_STEP_import_inventory_v1.json"


def vba_path(path: Path) -> str:
    # VBA string literals do not use backslash as an escape character.
    return str(path.resolve())


def main() -> int:
    if not STEP_PATH.is_file():
        raise FileNotFoundError(STEP_PATH)
    if PROJECT_PATH.exists():
        raise FileExistsError(f"Refusing to overwrite existing project: {PROJECT_PATH}")

    sys.path.insert(0, str(CST_PYTHON_LIB))
    import cst  # noqa: F401
    import _cst_interface as cst_native

    running = list(cst_native.running_design_environments())
    if len(running) != 1:
        raise RuntimeError(f"Expected exactly one running CST design environment: {running}")

    design_environment = cst_native.DesignEnvironment.connect(running[0])
    if not design_environment.has_active_project():
        raise RuntimeError("Running CST has no active project")
    project = design_environment.active_project()
    active_filename = Path(project.filename()) if project.filename() else None
    if active_filename and not active_filename.stem.startswith("Untitled_"):
        raise RuntimeError(
            "Active CST project is not the expected helper-owned Untitled project: "
            + project.filename()
        )

    PROJECT_PATH.parent.mkdir(parents=True, exist_ok=True)
    model3d = project.model3d

    model3d.add_to_history(
        "01_units_for_step_import",
        """With Units
    .Geometry \"mm\"
    .Frequency \"GHz\"
    .Time \"ns\"
End With""",
    )

    model3d.add_to_history(
        "10_import_SW_AP214_quadrotor_with_healing",
        f"""With STEP
    .Reset
    .FileName \"{vba_path(STEP_PATH)}\"
    .Id \"1\"
    .Version \"11.0\"
    .Healing \"True\"
    .ScaleToUnit \"True\"
    .ImportToActiveCoordinateSystem \"False\"
    .ImportCurves \"False\"
    .ImportAttributes \"True\"
    .Read
End With""",
    )
    model3d.Rebuild()

    tree = list(model3d.get_tree_items() or [])
    component_items = sorted(item for item in tree if item.startswith("Components\\"))
    leaf_items = sorted(
        item
        for item in component_items
        if not any(other.startswith(item + "\\") for other in component_items)
    )
    material_items = sorted(item for item in tree if item.startswith("Materials\\"))
    history_expected = {
        "01_units_for_step_import",
        "10_import_SW_AP214_quadrotor_with_healing",
    }

    if len(leaf_items) != 17:
        raise RuntimeError(
            f"CST imported {len(leaf_items)} leaf geometry objects; expected 17. "
            f"Objects={leaf_items}"
        )
    if model3d.is_solver_running():
        raise RuntimeError("Unexpected solver activity detected")

    project.save(str(PROJECT_PATH), include_results=False, allow_overwrite=False)

    saved_tree = list(model3d.get_tree_items() or [])
    messages = project.get_messages()
    inventory = {
        "status": "imported_geometry_only",
        "cst_pid": design_environment.pid(),
        "cst_version": design_environment.version(),
        "source_step": str(STEP_PATH),
        "project_path": str(PROJECT_PATH),
        "history_entries_expected": sorted(history_expected),
        "component_tree_items": component_items,
        "leaf_geometry_items": leaf_items,
        "leaf_geometry_count": len(leaf_items),
        "material_tree_items": material_items,
        "tree_item_count": len(saved_tree),
        "healing_requested": True,
        "scale_to_active_mm_unit": True,
        "import_to_active_coordinate_system": False,
        "import_attributes": True,
        "solver_configured_by_script": False,
        "solver_running": bool(model3d.is_solver_running()),
        "solver_run_by_script": False,
        "rcs_configured_by_script": False,
        "project_left_open_by_user_policy": True,
        "messages": messages,
    }
    INVENTORY_PATH.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(inventory, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
