"""Create the EM-Trace Stage 4B-2A geometry-only CST project.

CST 2024's Python 3.11 wrapper has a known-local ``__class__`` conversion
failure on this workstation. The script therefore uses the same official
``_cst_interface`` object returned below that wrapper. It does not configure
or run a solver, excitation, mesh, monitor, boundary, or RCS task.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


# Configure this local CST library path before execution.
CST_PYTHON_LIB = None
# Configure this local CST executable path before execution.
CST_EXE = None
REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECT_PATH = (
    REPO_ROOT
    / "simulation"
    / "cst"
    / "work"
    / "single_blade"
    / "EM-Trace_single_blade_test.cst"
)


def start_and_connect():
    sys.path.insert(0, str(CST_PYTHON_LIB))
    import cst  # noqa: F401  # Adds the CST binary directory to sys.path.
    import _cst_interface as cst_native

    running = list(cst_native.running_design_environments())
    if len(running) == 1:
        return cst_native.DesignEnvironment.connect(running[0]), None
    if len(running) > 1:
        raise RuntimeError(f"More than one CST session is running: {running}")

    before = set(running)
    process = subprocess.Popen([str(CST_EXE), "--m"])
    deadline = time.time() + 90
    last_error: Exception | None = None
    while time.time() < deadline:
        candidates = set(cst_native.running_design_environments()) - before
        if process.pid in candidates:
            try:
                return cst_native.DesignEnvironment.connect(process.pid), process
            except Exception as exc:  # CST may be registered before IPC is ready.
                last_error = exc
        time.sleep(1)
    process.terminate()
    raise TimeoutError(
        f"CST PID {process.pid} did not become connectable within 90 seconds: {last_error}"
    )


def main() -> int:
    if not CST_PYTHON_LIB.is_dir() or not CST_EXE.is_file():
        raise RuntimeError("Configure CST_PYTHON_LIB and CST_EXE locally before execution.")
    if PROJECT_PATH.exists():
        raise FileExistsError(f"Refusing to overwrite existing project: {PROJECT_PATH}")

    PROJECT_PATH.parent.mkdir(parents=True, exist_ok=True)
    design_environment = None
    project = None
    process = None
    saved = False

    try:
        design_environment, process = start_and_connect()
        if not design_environment.has_active_project():
            raise RuntimeError("CST started without the expected empty MWS project")
        project = design_environment.active_project()

        model3d = project.model3d
        resumed_existing_model = bool(model3d.DoesParameterExist("blade_length"))

        if not resumed_existing_model:
            model3d.add_to_history(
                "01_units_and_coordinates",
                """With Units
    .Geometry \"mm\"
    .Frequency \"GHz\"
    .Time \"ns\"
End With""",
            )

            model3d.add_to_history(
                "02_single_blade_parameters",
                """StoreParameter \"blade_length\", \"100\"
StoreParameter \"mount_extension\", \"4\"
StoreParameter \"blade_width\", \"18\"
StoreParameter \"blade_thickness\", \"2\"
StoreParameter \"blade_root_offset\", \"12\"
StoreParameter \"rotation_center_x\", \"0\"
StoreParameter \"rotation_center_y\", \"0\"
StoreParameter \"rotation_center_z\", \"0\"
StoreParameter \"theta_blade\", \"0\"
StoreParameter \"blade_attach_extension\", \"mount_extension\"
StoreParameter \"blade_solid_rmin\", \"blade_root_offset-blade_attach_extension\"
StoreParameter \"blade_tip_radius\", \"blade_root_offset+blade_length\"
StoreParameter \"blade_solid_length\", \"blade_tip_radius-blade_solid_rmin\"
StoreParameter \"blade_xmin\", \"rotation_center_x+blade_solid_rmin\"
StoreParameter \"blade_xmax\", \"rotation_center_x+blade_tip_radius\"
StoreParameter \"blade_ymin\", \"rotation_center_y-blade_width/2\"
StoreParameter \"blade_ymax\", \"rotation_center_y+blade_width/2\"
StoreParameter \"blade_zmin\", \"rotation_center_z-blade_thickness/2\"
StoreParameter \"blade_zmax\", \"rotation_center_z+blade_thickness/2\"""",
            )

            model3d.add_to_history(
                "10_create_component_rotor_r1",
                'Component.New "rotor_r1"',
            )
            model3d.add_to_history(
                "20_create_blade_r1_b1",
                """With Brick
    .Reset
    .Name \"blade_r1_b1\"
    .Component \"rotor_r1\"
    .Material \"PEC\"
    .Xrange \"blade_xmin\", \"blade_xmax\"
    .Yrange \"blade_ymin\", \"blade_ymax\"
    .Zrange \"blade_zmin\", \"blade_zmax\"
    .Create
End With""",
            )

        model3d.Rebuild()

        expected_parameters = {
            "blade_length": 100.0,
            "mount_extension": 4.0,
            "blade_width": 18.0,
            "blade_thickness": 2.0,
            "blade_xmin": 8.0,
            "blade_xmax": 112.0,
            "blade_ymin": -9.0,
            "blade_ymax": 9.0,
            "blade_zmin": -1.0,
            "blade_zmax": 1.0,
        }
        parameter_values = {
            str(model3d.GetParameterName(index)): float(
                model3d.GetParameterNValue(index)
            )
            for index in range(int(model3d.GetNumberOfParameters()))
        }
        actual_parameters = {
            name: parameter_values[name] for name in expected_parameters
        }
        if actual_parameters != expected_parameters:
            raise RuntimeError(
                f"Parameter verification failed: expected={expected_parameters}, "
                f"actual={actual_parameters}"
            )

        tree_items = list(model3d.get_tree_items() or [])
        required_tree_items = {
            r"Components\rotor_r1",
            r"Components\rotor_r1\blade_r1_b1",
            r"Materials\PEC",
            r"Materials\Vacuum",
        }
        missing_tree_items = required_tree_items.difference(tree_items)
        if missing_tree_items:
            raise RuntimeError(f"Missing CST tree items: {sorted(missing_tree_items)}")

        solver_running = bool(model3d.is_solver_running())
        if solver_running:
            raise RuntimeError("Unexpected solver activity detected")

        project.save(str(PROJECT_PATH), include_results=False, allow_overwrite=False)
        saved = True

        print(
            json.dumps(
                {
                    "status": "created",
                    "project_path": str(PROJECT_PATH),
                    "cst_pid": design_environment.pid(),
                    "cst_version": design_environment.version(),
                    "messages": project.get_messages(),
                    "resumed_existing_model": resumed_existing_model,
                    "verified_parameters": actual_parameters,
                    "verified_tree_items": sorted(required_tree_items),
                    "solver_type_default": model3d.GetSolverType(),
                    "solver_configured": False,
                    "solver_running": solver_running,
                    "solver_run_by_this_script": False,
                    "rcs_configured": False,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0
    finally:
        if project is not None and saved:
            project.close()
        if design_environment is not None:
            design_environment.close()
        elif process is not None and process.poll() is None:
            process.terminate()
        if not saved and PROJECT_PATH.exists():
            raise RuntimeError(
                "CST left a partial project after failure; inspect before reuse: "
                f"{PROJECT_PATH}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
