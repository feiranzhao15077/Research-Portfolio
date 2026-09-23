"""Extract complex monostatic HH scattering samples from Stage 6B CST projects.

The script reuses the single retained CST DesignEnvironment, opens each solved
angle project explicitly, reads the 10 GHz plane-wave far-field at the fixed
backscatter direction, and leaves CST itself running when extraction finishes.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_ROOT = REPO_ROOT / "results/raw/single_rotor_angle_response_v1"
OUTPUT_ROOT = REPO_ROOT / "results/raw/rotor_microdoppler_pipeline_v1"
RUN_ROOT = REPO_ROOT / "simulation/cst/runs/single_rotor_angle_response_v1"
ANGLES_DEG = tuple(range(0, 181, 15))
RECOVERY_CASES = {120: "theta_120_retry1", 150: "theta_150_retry1", 180: "theta_180_retry1"}
OBSERVATION_THETA_DEG = 90.0
OBSERVATION_PHI_DEG = 180.0
INCIDENT_DIRECTION = "+X [1,0,0]"
POLARIZATION_BASIS = "CST spherical E_phi; at theta=90, phi=180, e_phi=-Y_global"
LOCAL_EPHI_TO_GLOBAL_POSITIVE_Y_SIGN = -1.0


def import_cst_native():
    # Configure this local CST library path before execution.
    cst_python_lib = None
    if str(cst_python_lib) not in sys.path:
        sys.path.insert(0, str(cst_python_lib))
    import cst.interface  # type: ignore[import-not-found]

    return cst.interface


def case_id(angle_deg: int) -> str:
    return RECOVERY_CASES.get(angle_deg, f"theta_{angle_deg:03d}")


def case_project(angle_deg: int) -> Path:
    ident = case_id(angle_deg)
    return RUN_ROOT / ident / f"EM-Trace_single_rotor_{ident}_10GHz_HH_v1.cst"


def get_scalar(plot, key: str) -> float:
    """Read one calculated far-field list value using documented macro keys."""
    try:
        values = list(plot.GetList(key))
        if values:
            return float(values[0])
    except RuntimeError:
        pass
    return float(plot.GetListItem(0, key))


def extract_one(model3d, tree_item: str) -> dict[str, float | str]:
    model3d.SelectTreeItem(tree_item)
    plot = model3d.FarfieldPlot

    # Read the complex field in the same linear efield mode used by CST's
    # installed GRASP far-field export macro.  The RCS/dB plot mode must not be
    # used for ph_re/ph_im because its display floor contaminates those keys.
    plot.Reset()
    plot.SetPlotMode("efield")
    plot.SetScaleLinear(True)
    plot.UseFarfieldApproximation(True)
    plot.Origin("zero")
    plot.AddListEvaluationPoint(
        OBSERVATION_THETA_DEG, OBSERVATION_PHI_DEG, 0, "spherical", "", 0
    )
    plot.CalculateList("")
    ephi_re = get_scalar(plot, "ph_re")
    ephi_im = get_scalar(plot, "ph_im")

    # Re-evaluate RCS magnitude independently, matching the established Stage
    # 6B extraction settings exactly.
    plot.Reset()
    plot.SetPlotMode("rcs")
    plot.Plottype("polar")
    plot.Vary("phi")
    plot.Theta(90)
    plot.Step(1)
    plot.SetFrequency("10")
    plot.SetScaleLinear(False)
    plot.DBUnit("0")
    plot.UseFarfieldApproximation(True)
    plot.Origin("bbox")
    plot.Plot()
    plot.AddListEvaluationPoint(
        OBSERVATION_THETA_DEG, OBSERVATION_PHI_DEG, 0, "spherical", "", 0
    )
    plot.CalculateList("")
    rcs_dbsm = get_scalar(plot, "spherical linear phi abs")
    rcs_m2 = 10 ** (rcs_dbsm / 10.0)
    phase_rad = math.atan2(ephi_im, ephi_re)
    scattering_magnitude_m = math.sqrt(rcs_m2)
    scattering_re_m = scattering_magnitude_m * math.cos(phase_rad)
    scattering_im_m = scattering_magnitude_m * math.sin(phase_rad)
    global_y_re = LOCAL_EPHI_TO_GLOBAL_POSITIVE_Y_SIGN * ephi_re
    global_y_im = LOCAL_EPHI_TO_GLOBAL_POSITIVE_Y_SIGN * ephi_im
    global_y_phase_rad = math.atan2(global_y_im, global_y_re)
    observation_theta = get_scalar(plot, "Point_T")
    observation_phi = get_scalar(plot, "Point_P")
    return {
        "theta_obs_deg": observation_theta,
        "phi_obs_deg": observation_phi,
        "observation_theta": observation_theta,
        "observation_phi": observation_phi,
        "incident_direction": INCIDENT_DIRECTION,
        "polarization_basis": POLARIZATION_BASIS,
        "local_to_global_sign": LOCAL_EPHI_TO_GLOBAL_POSITIVE_Y_SIGN,
        "ephi_re": ephi_re,
        "ephi_im": ephi_im,
        "ephi_magnitude": math.hypot(ephi_re, ephi_im),
        "ephi_phase_rad": phase_rad,
        "ephi_phase_deg": math.degrees(phase_rad),
        "global_y_re": global_y_re,
        "global_y_im": global_y_im,
        "global_y_phase_rad": global_y_phase_rad,
        "global_y_phase_deg": math.degrees(global_y_phase_rad),
        "rcs_dbsm": rcs_dbsm,
        "rcs_m2_from_db": rcs_m2,
        "scattering_re_m": scattering_re_m,
        "scattering_im_m": scattering_im_m,
        "scattering_global_y_re_m": LOCAL_EPHI_TO_GLOBAL_POSITIVE_Y_SIGN * scattering_re_m,
        "scattering_global_y_im_m": LOCAL_EPHI_TO_GLOBAL_POSITIVE_Y_SIGN * scattering_im_m,
        "scattering_magnitude_m": scattering_magnitude_m,
        "scattering_magnitude_squared_m2": scattering_re_m**2 + scattering_im_m**2,
    }


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    cst_native = import_cst_native()
    running = list(cst_native.running_design_environments())
    if len(running) != 1:
        raise RuntimeError(f"Expected exactly one retained CST session, found: {running}")

    de = cst_native.DesignEnvironment.connect(running[0])
    rows: list[dict[str, object]] = []
    for angle_deg in ANGLES_DEG:
        project_path = case_project(angle_deg)
        if not project_path.is_file():
            raise FileNotFoundError(project_path)
        project = None
        try:
            project = de.open_project(str(project_path))
            opened = Path(project.filename()).resolve()
            if opened != project_path.resolve():
                raise RuntimeError(f"Opened wrong CST project: {opened} != {project_path.resolve()}")
            model3d = project.model3d
            tree = list(model3d.get_tree_items() or [])
            farfields = [
                item
                for item in tree
                if item.startswith("Farfields\\") and "f=10" in item and "[pw]" in item
            ]
            if not farfields:
                raise RuntimeError(f"10 GHz plane-wave far-field missing: {project_path}")
            run_info = model3d.get_solver_run_info()
            if run_info.get("state") != "SUCCESS":
                raise RuntimeError(f"Unsuccessful CST run in {project_path}: {run_info}")
            sample = extract_one(model3d, farfields[0])
            summary_path = RAW_ROOT / case_id(angle_deg) / "solver_run_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            reference_dbsm = float(summary["rcs"]["rcs_copol_dbsm"])
            sample.update(
                {
                    "rotor_angle_deg": angle_deg,
                    "frequency_ghz": 10.0,
                    "polarization": "HH",
                    "receive_component": "Ephi",
                    "source_case_id": case_id(angle_deg),
                    "source_project": str(project_path.relative_to(REPO_ROOT)),
                    "farfield_tree": farfields[0],
                    "reference_rcs_dbsm": reference_dbsm,
                    "rcs_recheck_error_db": float(sample["rcs_dbsm"]) - reference_dbsm,
                }
            )
            rows.append(sample)
            print(
                f"EXTRACTED theta={angle_deg:03d} "
                f"Ephi=({sample['ephi_re']:+.9e},{sample['ephi_im']:+.9e}) "
                f"phase={sample['ephi_phase_deg']:+.3f}deg",
                flush=True,
            )
        finally:
            if project is not None:
                project.close()

    csv_path = OUTPUT_ROOT / "complex_scattering_snapshots.csv"
    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    max_recheck_error = max(abs(float(row["rcs_recheck_error_db"])) for row in rows)
    power_ratios = [
        float(row["scattering_magnitude_squared_m2"]) / float(row["rcs_m2_from_db"])
        for row in rows
        if float(row["rcs_m2_from_db"]) > 0
    ]
    metadata = {
        "dataset_id": "rotor_microdoppler_pipeline_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_stage": "Stage 6B",
        "sample_count": len(rows),
        "angle_range_deg": [0, 180],
        "angle_step_deg": 15,
        "frequency_ghz": 10.0,
        "polarization": "HH",
        "receive_component": "Ephi",
        "observation": {
            "theta_deg": OBSERVATION_THETA_DEG,
            "phi_deg": OBSERVATION_PHI_DEG,
            "monostatic": True,
        },
        "incident_direction": INCIDENT_DIRECTION,
        "polarization_basis": POLARIZATION_BASIS,
        "local_to_global_sign": LOCAL_EPHI_TO_GLOBAL_POSITIVE_Y_SIGN,
        "complex_field_keys": {"real": "ph_re", "imaginary": "ph_im"},
        "complex_scattering_definition": "sqrt(RCS_m2) * exp(j * angle(Ephi_linear_local))",
        "global_positive_y_definition": "E_global_y = -E_phi at theta=90 deg, phi=180 deg",
        "phase_reference_origin": "global coordinate origin",
        "reference_macro": "Installed CST GRASP far-field export macro; local installation path omitted",
        "maximum_rcs_recheck_error_db": max_recheck_error,
        "scattering_power_to_rcs_m2_ratio_range": [min(power_ratios), max(power_ratios)],
        "cst_session_left_open": True,
        "warnings": [
            "The 15 degree grid is an extraction/pipeline baseline, not an alias-free micro-Doppler angular grid.",
            "The complex Ephi normalization is preserved exactly as returned by CST; downstream processing normalizes amplitude explicitly.",
        ],
    }
    (OUTPUT_ROOT / "snapshot_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)
    print("CST_SESSION_LEFT_OPEN after complex snapshot extraction", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
