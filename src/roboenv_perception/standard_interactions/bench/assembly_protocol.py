"""Deterministic tool-mediated assembly tests, with no asset state writes.

The XYZ carriage and lid pull are ideal bench actuators using privileged poses.
Thread release is owned by the unchanged asset runtime. Removed screws are
carried outside the enclosure and released under gravity, not teleported.
"""

import json
import math
import time
from pathlib import Path

import numpy as np
import torch

try:
    from screwdriving import rotate
except ModuleNotFoundError:
    from roboenv_perception.tools.screwdriving import rotate


def lid_pull_force(world_height, baseline_height, world_vertical_velocity, elapsed):
    """A force-limited world-frame actuator, independent of enclosure motion."""
    force = 1000 * (.04 - (world_height - baseline_height)) - 20 * world_vertical_velocity
    return max(0., min(force, 20 * min(1., max(0., elapsed) / .3)))


def run_protocol(args, *, stage, sim, state, view, mechanics, art, railids,
                 face, lid, body, contacts):
    dt = args.dt
    indices = torch.tensor([0], dtype=torch.int32)
    origin = np.asarray(face, dtype=float)
    order = [int(value) for value in args.order.split(',')][:args.remove_count]
    park = np.array([.30 - origin[0], .23 - origin[1], .06])
    target = np.zeros(3)
    trace, phases, probes, removals = [], [], [], []
    phase, entered, serial, selected = 'park', 0., 0, None
    clear_target = None
    baseline = None
    blocked = None
    started = time.monotonic()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    def pose(rigid):
        row = rigid.get_transforms()[0].cpu().numpy().copy()
        return row[:3], np.array([row[6], *row[3:6]])

    def local_point(point, position, quaternion):
        return rotate(quaternion * np.array([1, -1, -1, -1]), point - position)

    def switch(name, now):
        nonlocal phase, entered
        phase, entered = name, now
        phases.append({'phase': name, 't': now, 'screw': selected})
        print(json.dumps(phases[-1]), flush=True)

    def remaining():
        return [i for i, screw in enumerate(mechanics.screws) if screw.model.thread_active]

    def baseline_geometry():
        bp, bq = pose(body)
        lp, lq = pose(lid)
        anchors = []
        for screw in mechanics.screws:
            sp, _ = pose(screw.screw)
            anchors.append(local_point(sp, lp, lq))
        return {'lid_local_anchors': [p.tolist() for p in anchors],
                'body_local_anchors': [local_point(lp + rotate(lq, p), bp, bq).tolist()
                                       for p in anchors],
                'lid_relative_position': local_point(lp, bp, bq).tolist(),
                'lid_world_position': lp.tolist()}

    def geometry():
        bp, bq = pose(body)
        lp, lq = pose(lid)
        if baseline is None:
            return None
        anchor_deltas = [local_point(lp + rotate(lq, p), bp, bq) - np.asarray(q)
                         for p, q in zip(baseline['lid_local_anchors'],
                                         baseline['body_local_anchors'])]
        return {'anchor_displacement_body_m': [p.tolist() for p in anchor_deltas],
                'lid_relative_displacement_m': (local_point(lp, bp, bq) -
                                                baseline['lid_relative_position']).tolist()}

    def measured_sample(now, pull, press):
        screw_rows = []
        bp, bq = pose(body)
        for screw in mechanics.screws:
            sp, _ = pose(screw.screw)
            z = float((local_point(sp, bp, bq) - screw.support_origin)[2])
            screw_rows.append({'pose': screw.screw.get_transforms()[0].cpu().numpy().tolist(),
                               'velocity': screw.screw.get_velocities()[0].cpu().numpy().tolist(),
                               'theta_rad': screw.model.theta, 'exit_m': z,
                               'thread_active': screw.model.thread_active,
                               'connector_enabled': screw.guide_joint.GetJointEnabledAttr().Get(),
                               'state': screw.model.state,
                               'helix_residual_m': z - screw.model.theta * screw.model.p.lead_m / math.tau})
        return {'t': now, 'phase': phase, 'selected': selected,
                'remaining': remaining(), 'screws': screw_rows,
                'lid_pose': lid.get_transforms()[0].cpu().numpy().tolist(),
                'lid_velocity': lid.get_velocities()[0].cpu().numpy().tolist(),
                'body_pose': body.get_transforms()[0].cpu().numpy().tolist(),
                'body_velocity': body.get_velocities()[0].cpu().numpy().tolist(),
                'geometry': geometry(), 'pull_N': pull, 'press_N': press,
                'carriage_target_m': target.tolist(),
                'tool': json.loads(json.dumps(state.tool_status)),
                'native_joint_wrenches_child_frames': art.get_link_incoming_joint_force()[0].cpu().numpy().tolist(),
                'lid_body_normal_force_N': contacts.get_contact_force_matrix(dt).cpu().numpy().tolist()}

    sample_every = max(1, round(.02 / dt))
    last_now = 0.
    failure = None
    try:
        for step in range(round(args.seconds / dt)):
            now = step * dt
            last_now = now
            elapsed = now - entered
            desired = park.copy()
            enabled, rpm, release = False, 0., False
            pull = 0.
            press = args.press if args.boundary == 'pressed' and phase not in {'probe', 'done'} else 0.
            if phase == 'park':
                if np.max(np.abs(target - park)) < .0001 and elapsed > 2.:
                    if baseline is None:
                        baseline = baseline_geometry()
                    switch('probe', now)
            elif phase == 'probe':
                g = geometry()
                lp, _ = pose(lid)
                vertical_velocity = float(lid.get_velocities()[0, 2].cpu())
                # Finite force, compliant 40mm lift target. No pose servo on lid.
                # The target is in the WORLD bench frame: using lid-to-body
                # displacement here would keep lifting the whole free assembly.
                pull = lid_pull_force(lp[2], baseline['lid_world_position'][2],
                                      vertical_velocity, elapsed)
                if elapsed >= 1.5:
                    probes.append({'t': now, 'removed_count': serial,
                                   'remaining': remaining(), 'geometry': g,
                                   'pull_cap_N': 20., 'tool_parked': True})
                    if serial >= len(order):
                        switch('done', now)
                    else:
                        selected = order[serial]
                        switch('approach_high' if args.boundary == 'fixed' else 'post_probe_settle', now)
            elif phase == 'post_probe_settle':
                # A free enclosure can lift as a whole during the pull control.
                # Let it land under gravity before testing tool access; do not
                # reset its pose or chase an airborne box with the carriage.
                bp, _ = pose(body)
                speed_now = np.linalg.norm(body.get_velocities()[0].cpu().numpy()[:3])
                if elapsed > 3. and abs(bp[2]) < .005 and speed_now < .02:
                    switch('approach_high', now)
                elif elapsed > 8.:
                    blocked = {'phase': phase, 't': now,
                               'reason': 'free enclosure did not settle after pull control'}
                    switch('blocked', now)
            elif phase == 'done':
                break
            else:
                screw = mechanics.screws[selected]
                sp, _ = pose(screw.screw)
                approach = sp - origin
                # Gravity compensation and the existing nominal 2.7mm insertion.
                approach[2] += .32 * 9.81 / 5000. - .0012
                if args.fault == 'offset':
                    approach[0] += .004
                elif args.fault == 'gap':
                    approach[2] += .012
                if phase == 'approach_high':
                    desired = approach.copy()
                    desired[2] = .06
                    if np.max(np.abs(target - desired)) < .0001 and elapsed > 1.:
                        switch('engage', now)
                elif phase in {'engage', 'spin'}:
                    desired = approach
                    enabled = args.fault != 'motor_off'
                    # Negative controls exercise the requested motor command,
                    # not merely the slow phase-search motion of the controller.
                    rpm = (-4. if phase == 'engage' and args.assembly_case != 'negative'
                           else -args.rpm)
                    engaged = screw.hex.engaged
                    if phase == 'engage' and engaged and elapsed > .5:
                        switch('spin', now)
                    if not screw.model.thread_active:
                        clear_target = target.copy()
                        switch('extract', now)
                    elif elapsed >= args.stage_timeout:
                        blocked = {'phase': phase, 'screw': selected, 't': now,
                                   'reason': 'stage timeout without thread release'}
                        switch('negative_finish' if args.assembly_case == 'negative' else 'blocked', now)
                    if args.assembly_case == 'negative' and elapsed >= 6.:
                        switch('negative_finish', now)
                elif phase == 'extract':
                    desired = clear_target.copy()
                    desired[2] += min(.045, elapsed * .003)
                    if screw.model.state == 'removed' and elapsed > 6.:
                        removals.append({'screw': selected, 't': now,
                                         'pose': screw.screw.get_transforms()[0].cpu().numpy().tolist(),
                                         'rotation_rad': screw.model.theta})
                        clear_target = target.copy()
                        switch('carry', now)
                    elif elapsed > 18.:
                        blocked = {'phase': phase, 'screw': selected, 't': now,
                                   'reason': 'thread clear but screw not fully removed'}
                        switch('blocked', now)
                elif phase == 'carry':
                    desired = park.copy()
                    # Raise above the remaining heads before moving sideways.
                    if elapsed < 2.:
                        desired[:2] = clear_target[:2]
                    if elapsed > 2. and np.max(np.abs(target - park)) < .0001:
                        switch('deposit', now)
                elif phase == 'deposit':
                    release = True
                    if elapsed > 1.:
                        serial += 1
                        switch('park', now)
                elif phase in {'negative_finish', 'blocked'}:
                    if elapsed > .5:
                        break

            # Explicit ideal carriage joint targets; never set body/part states.
            speed = np.array([.20, .20, .07])
            if phase == 'extract':
                speed[:] = .10
            target += np.clip(desired - target, -speed * dt, speed * dt)
            command = np.zeros((1, art.max_dofs), dtype=np.float32)
            command[0, railids] = target
            art.set_dof_position_targets(torch.tensor(command), indices)
            state.tool_command = {'enabled': enabled, 'rpm': rpm, 'release': release}
            if phase == 'post_probe_settle':
                press *= min(1., (now - entered) / .5)
            external = np.array([0., 0., pull - press])
            lid.apply_forces_and_torques_at_position(
                torch.tensor([external], dtype=torch.float32), torch.zeros((1, 3)),
                None, indices, True)
            sim.step(render=False)
            if step % sample_every == 0:
                row = measured_sample(now, pull, press)
                trace.append(row)
                numeric = [*row['lid_pose'], *row['body_pose'], *row['lid_velocity'],
                           *row['body_velocity'], *[v for s in row['screws'] for v in s['pose'] + s['velocity']]]
                if not np.isfinite(numeric).all():
                    raise RuntimeError('Nonfinite physical state')
                if max(np.linalg.norm(s['velocity'][:3]) for s in row['screws']) > 30.:
                    raise RuntimeError('Screw speed exceeds declared 30m/s divergence guard')
                if (max(np.linalg.norm(row[key][:3]) for key in ('body_velocity', 'lid_velocity')) > 10.
                        or max(abs(v) for key in ('body_pose', 'lid_pose') for v in row[key][:3]) > 2.):
                    raise RuntimeError('Enclosure left the declared bench workspace/speed range')
            if step % max(1, round(1 / dt)) == 0:
                out.with_suffix('.progress.json').write_text(json.dumps(trace[-1], indent=2))
                print(f'progress sim={now:.1f}s phase={phase} removed={serial} wall={time.monotonic()-started:.1f}s', flush=True)
    except Exception as exc:
        failure = f'{type(exc).__name__}: {exc}'
        import traceback
        traceback.print_exc()
    report = {'schema': 'envcreator.assembly-bench.v1', 'case': args.assembly_case,
              'boundary': args.boundary, 'fault': args.fault, 'order': order,
              'rpm': args.rpm, 'dt': dt, 'press_N': args.press if args.boundary == 'pressed' else 0.,
              'completed': phase in {'done', 'negative_finish'}, 'final_phase': phase,
              'failure': failure, 'blocked': blocked, 'sim_seconds': last_now,
              'wall_seconds': time.monotonic() - started, 'baseline': baseline,
              'probes': probes, 'removals': removals, 'phases': phases,
              'trace': trace, 'events': state.events,
              'synchronous_releases': getattr(getattr(state, 'release_observer', None), 'records', []),
              'release_observation_schema': 'envcreator.release-observation/1' if getattr(state, 'release_observer', None) else None,
              'native_link_names': list(art.shared_metatype.link_names),
              'runtime_pose_writes': False, 'forced_thread_release': False,
              'pull_target_frame': 'world', 'external_press_direction': 'world -Z',
              'observation_permissions': 'privileged geometry, joint and runtime state',
              'scope': 'ideal XYZ carriage, optional fixed base or prescribed lid press; compliant 20N lid pull; no robot or real grasp; geometry-gated socket and capped ideal magnet'}
    out.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({k: report[k] for k in ('completed', 'final_phase', 'failure', 'sim_seconds', 'wall_seconds')}), flush=True)
