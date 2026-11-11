import test from 'node:test';
import assert from 'node:assert/strict';
import { measuredQuiescence } from '../src/roboenv_perception/integration/smoke.mjs';

test('missing execution or an undrained controller never proves release', () => {
  for (const sample of [null, {}, { execution: {} },
    { execution: { controllerQuiescent: true, resourcesReleased: false } },
    { execution: { state: 'active', resourcesReleased: true } }]) {
    assert.equal(measuredQuiescence(sample), false);
  }
  assert.equal(measuredQuiescence({ execution: { controllerQuiescent: true, resourcesReleased: true } }), true);
});

test('an idle controller needs complete finite, quiet physical measurements', () => {
  const execution = { state: 'idle', resourcesReleased: true };
  const measurements = { linearSpeedMps: 0, angularSpeedRadps: .01, upperJointSpeedRadps: .02 };
  assert.equal(measuredQuiescence({ execution, measurements }), true);
  for (const patch of [{ linearSpeedMps: 2 }, { angularSpeedRadps: NaN },
    { upperJointSpeedRadps: undefined }, { linearSpeedMps: -.1 }]) {
    assert.equal(measuredQuiescence({ execution, measurements: { ...measurements, ...patch } }), false);
  }
});
