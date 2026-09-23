import numpy as np
from src.geometry import SceneGeometry, path_lengths, specular_point


def test_image_method_matches_two_segment_reflection():
    scene = SceneGeometry(np.array([0.0, 0.0, 5.0]), np.array([200.0, 0.0, 20.0]))
    p = np.array([200.0, 10.0, 20.0])
    rd, rr = path_lengths(p, scene)
    hit = specular_point(p, scene)
    two_segment = np.linalg.norm(scene.radar_position_m - hit) + np.linalg.norm(p - hit)
    assert np.isclose(rr, two_segment)
    assert rd > 0 and rr > rd
