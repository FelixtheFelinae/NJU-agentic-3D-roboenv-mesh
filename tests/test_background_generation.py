"""Mask protection, immutable evidence and optional-stage failure semantics, without a model."""

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from roboenv_perception.construction.background_generation import (
    BackgroundGenerationConfig,
    generate_background,
    prepare_canvas,
)
from roboenv_perception.construction.common import verify_run
from roboenv_perception.harness.run import HarnessSettings, _background
from roboenv_perception.io import read_json, write_json


def _config(**kwargs):
    return BackgroundGenerationConfig(model_path=Path('/missing-local-model'), prompt='An empty study room',
                                      width=256, height=256, **kwargs)


def _inputs(tmp_path):
    image = np.full((128, 128, 3), [40, 80, 120], dtype=np.uint8)
    image[32:96, 32:96] = [255, 0, 255]  # Excluded task asset must not appear in the condition.
    observed = np.ones((128, 128), dtype=np.uint8) * 255
    observed[28:100, 28:100] = 0
    source = tmp_path/'source.png'
    mask = tmp_path/'mask.png'
    Image.fromarray(image).save(source)
    Image.fromarray(observed).save(mask)
    return source, mask


def test_condition_excludes_foreground_and_marks_padding(tmp_path):
    image, mask = _inputs(tmp_path)
    canvas = prepare_canvas(Image.open(image), np.asarray(Image.open(mask)) > 0, _config())
    rgb = np.asarray(canvas['image'])
    assert not np.any((rgb[..., 0] == 255) & (rgb[..., 2] == 255))
    assert canvas['provenance'][0, 0] == 0
    assert set(np.unique(canvas['provenance'])) == {0, 1, 2}
    assert canvas['mask'].getpixel((0, 0)) == 255
    assert canvas['protected'].any()


def test_generation_preserves_observed_pixels_and_binds_evidence(tmp_path, monkeypatch):
    source, mask = _inputs(tmp_path)

    def fake_worker(command, **kwargs):
        root = Path(command[-1])
        Image.new('RGB', (256, 256), (200, 180, 160)).save(root/'raw.png')
        write_json(root/'generation.json', {'status': 'generated', 'backend': 'offline-test-fixture'})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, 'run', fake_worker)
    output = tmp_path/'result'
    result = generate_background(source, mask, output, _config())
    assert result['protected_pixels_unchanged']
    assert result['projection'] == 'perspective_image_not_panorama'
    assert result['visual_review'] == 'pending'
    assert verify_run(output, 'generation.json')['output_sha256'] == result['output_sha256']
    provenance = np.asarray(Image.open(output/'provenance.png'))
    generated = np.asarray(Image.open(output/'background.png'))
    condition = np.asarray(Image.open(output/'input.png'))
    assert np.array_equal(generated[provenance == 1], condition[provenance == 1])
    assert np.all(generated[provenance == 0] == [200, 180, 160])


def test_worker_failure_is_never_a_success(tmp_path, monkeypatch):
    source, mask = _inputs(tmp_path)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=2))
    output = tmp_path/'failed'
    with pytest.raises(RuntimeError, match='worker exited 2'):
        generate_background(source, mask, output, _config())
    assert read_json(output/'generation.json')['status'] == 'failed'
    assert read_json(output/'run.json')['status'] == 'failed'
    assert not (output/'background.png').exists()


def test_inconsistent_masks_and_conflicting_opt_in_are_rejected(tmp_path):
    with pytest.raises(ValueError, match='contain real background'):
        prepare_canvas(Image.new('RGB', (32, 32)), np.zeros((32, 32), bool), _config())
    with pytest.raises(ValueError, match='multiples of 8'):
        BackgroundGenerationConfig(model_path=Path('/x'), prompt='room', width=513)
    with pytest.raises(ValueError, match='background_mode=off'):
        HarnessSettings(perception_config=Path('p'), construction_config=Path('c'), artisan_python=Path('a'),
                        background_mode='off', background_generation_config=Path('generation.toml'))


def test_disabled_stage_does_not_import_generation_or_invoke_model(tmp_path, monkeypatch):
    import roboenv_perception.construction.background_generation as generation

    monkeypatch.setattr(generation, 'complete_prepared_background',
                        lambda *args, **kwargs: pytest.fail('disabled generation must never run'))
    settings = HarnessSettings(perception_config=Path('p'), construction_config=Path('c'),
                               artisan_python=Path('a'), background_mode='off')
    _background(tmp_path/'off', Path('unused'), Path('unused'), None, settings, time.monotonic()+60)
    assert read_json(tmp_path/'off/background_result.json') == {
        'status': 'not_run', 'stages': {}, 'physical_collision': False,
    }


def test_optional_generation_failure_preserves_prepared_background(tmp_path, monkeypatch):
    import roboenv_perception.construction.background as masks
    import roboenv_perception.construction.background_generation as generation

    prepared = []

    def prepare(*args):
        args[1].mkdir()
        prepared.append(args[1])

    def broken(*args, **kwargs):
        raise RuntimeError('local model unavailable')

    monkeypatch.setattr(masks, 'prepare_background', prepare)
    monkeypatch.setattr(generation, 'complete_prepared_background', broken)
    perception = tmp_path/'perception'
    write_json(perception/'scene.json', {'kind':'image'})
    config = tmp_path/'generation.toml'
    config.write_text('model_path="/missing"\nprompt="empty room"\n')
    settings = HarnessSettings(perception_config=Path('p'), construction_config=Path('c'),
                               artisan_python=Path('a'), background_mode='masks',
                               background_generation_config=config)
    _background(tmp_path/'background', perception, Path('unused'), SimpleNamespace(background=None),
                settings, time.monotonic()+60)
    report = read_json(tmp_path/'background/background_result.json')
    assert prepared and prepared[0].exists()
    assert report['generation_requested']
    assert report['generation']['status'] == 'failed'
    assert 'model unavailable' in report['generation']['error']
    assert report['stages']['masks'] == 'completed'


def test_display_layer_preserves_original_properties_with_real_usd(tmp_path):
    import os

    from roboenv_perception.construction.background_scene import attach_background

    value = os.environ.get('ENVCREATOR_USD_PYTHON')
    if not value:
        pytest.skip('Set ENVCREATOR_USD_PYTHON for a real OpenUSD display check')
    source = tmp_path/'source.usda'
    source.write_text('''#usda 1.0
(
 defaultPrim = "World"
 metersPerUnit = 1
 upAxis = "Z"
)
def Xform "World" {
 def Cube "Body" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI"]) {
  float physics:mass = 2
 }
 def Cube "Floor" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {}
 def Camera "Camera" {
  float focalLength = 30
  float horizontalAperture = 28
  float verticalAperture = 21
 }
}
''')
    image = tmp_path/'image.png'
    Image.new('RGB', (512, 256), 'blue').save(image)
    output = tmp_path/'display'
    report = attach_background(source, image, output, Path(value), '/World/Camera', 4)
    assert report['status'] == 'authored'
    assert all(report['checks'].values())
    assert report['image_fit'] == 'centre_crop_to_camera_aspect'
    assert report['uv_crop'][0] > 0  # Landscape source crops horizontally, never stretches.
    assert verify_run(output, 'display.json')['checks']['original_prims_unchanged']
    hidden = attach_background(source, image, tmp_path/'hidden-floor', Path(value),
                               '/World/Camera', 4, '/World/Floor')
    assert all(hidden['checks'].values())
    assert hidden['hidden_floor_visual'] == '/World/Floor'
    assert 'visibility = "invisible"' in (tmp_path/'hidden-floor/scene.usda').read_text()
    with pytest.raises(RuntimeError, match='display worker exited'):
        attach_background(source, image, tmp_path/'bad-display', Path(value), '/World/AbsentCamera', 4)
