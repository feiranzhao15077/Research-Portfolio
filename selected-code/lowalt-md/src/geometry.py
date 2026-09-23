"""Flat-ground monostatic geometry and image-method helpers."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class SceneGeometry:
    radar_position_m: np.ndarray
    rotor_center_m: np.ndarray

    def __post_init__(self) -> None:
        rp = np.asarray(self.radar_position_m, dtype=float)
        tp = np.asarray(self.rotor_center_m, dtype=float)
        if rp.shape != (3,) or tp.shape != (3,):
            raise ValueError("positions must be length-3 vectors")
        if rp[2] < 0 or tp[2] <= 0:
            raise ValueError("radar and rotor must be above z=0")
        object.__setattr__(self, "radar_position_m", rp)
        object.__setattr__(self, "rotor_center_m", tp)

    @property
    def mirrored_radar_m(self) -> np.ndarray:
        p = self.radar_position_m.copy()
        p[2] *= -1.0
        return p


def path_lengths(scatterer_positions_m: np.ndarray, scene: SceneGeometry) -> tuple[np.ndarray, np.ndarray]:
    """Return direct and image-method reflected one-way distances."""
    p = np.asarray(scatterer_positions_m, dtype=float)
    if p.shape[-1] != 3:
        raise ValueError("scatterer positions must end in dimension 3")
    direct = np.linalg.norm(p - scene.radar_position_m, axis=-1)
    reflected = np.linalg.norm(p - scene.mirrored_radar_m, axis=-1)
    return direct, reflected


def specular_point(scatterer_position_m: np.ndarray, scene: SceneGeometry) -> np.ndarray:
    """Intersection of image-radar to scatterer line with the ground plane."""
    p = np.asarray(scatterer_position_m, dtype=float)
    q = scene.mirrored_radar_m
    if p.shape != (3,) or p[2] <= 0:
        raise ValueError("scatterer must be a single point above ground")
    alpha = -q[2] / (p[2] - q[2])
    return q + alpha * (p - q)


def incidence_angle_rad(scatterer_position_m: np.ndarray, scene: SceneGeometry) -> np.ndarray:
    """Return ``theta_from_normal`` for the reflected ray, in radians.

    The image-method reflected leg runs from the mirrored radar to the
    scatterer.  The angle is measured from the upward ground normal (0 =
    normal incidence, pi/2 = grazing), so its vertical component is
    ``scatterer_z - mirrored_radar_z = z_k + h_r``.  The complementary
    grazing angle from the surface is ``pi/2 - theta_from_normal``.
    """
    p = np.asarray(scatterer_position_m, dtype=float)
    mirrored = scene.mirrored_radar_m
    reflected_vector = p - mirrored
    reflected = np.linalg.norm(reflected_vector, axis=-1)
    vertical_component = np.abs(reflected_vector[..., 2])
    cosine = vertical_component / np.maximum(reflected, 1e-30)
    return np.arccos(np.clip(cosine, 0.0, 1.0))


def fraunhofer_distance(diameter_m: float, wavelength_m: float) -> float:
    """Engineering Fraunhofer scale ``2 D**2 / wavelength`` in metres."""
    if not np.isfinite([diameter_m, wavelength_m]).all() or diameter_m <= 0 or wavelength_m <= 0:
        raise ValueError("diameter and wavelength must be finite and positive")
    return 2.0 * diameter_m ** 2 / wavelength_m


def rotor_max_dimension_m(blade_length_m: float, hub_extent_m: float = 0.0) -> float:
    """Return a conservative maximum rotor diameter for the far-field gate.

    ``blade_length_m`` is the rotor radius. ``hub_extent_m`` optionally adds
    the diameter of a modeled central body; it is zero for the current rotor
    disk abstraction.
    """
    if not np.isfinite([blade_length_m, hub_extent_m]).all() or blade_length_m <= 0 or hub_extent_m < 0:
        raise ValueError("blade length must be positive and hub extent non-negative")
    return 2.0 * blade_length_m + hub_extent_m
