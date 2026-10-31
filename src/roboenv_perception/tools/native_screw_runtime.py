"""Native scene mechanics, not a robot policy. Frozen with the body snapshot.

Reuses the bench's force-only thread and hex models. Every force is applied
before a PhysX substep; no poses or joint states are written. Magnetic retention
is an explicit capped compliant force, acquired only after geometric engagement.
"""
import json
import math
import time

import numpy as np

try:
    from .solver_thread import SolverThread
    from .fastener import Parameters, ScrewModel, implicit_screw_step
    from .screwdriving import EngagementParameters, HexEngagement, rotate, axial_contact, socket_contact_wrench, passive_spindle_torque, motor_drive_parameters
except ImportError:  # Standalone native body snapshot.
    from solver_thread import SolverThread
    from fastener import Parameters, ScrewModel, implicit_screw_step
    from screwdriving import EngagementParameters, HexEngagement, rotate, axial_contact, socket_contact_wrench, passive_spindle_torque, motor_drive_parameters


def multiply_q(a, b):
    aw, bw = a[0], b[0]
    av, bv = np.asarray(a[1:]), np.asarray(b[1:])
    return np.array([aw*bw-av@bv, *(aw*bv+bw*av+np.cross(av,bv))])


def support_reaction(force, screw_torque, tool_torque, screw_position, support_position):
    """Transport the mating-part reaction to the support COM."""
    reaction = -np.asarray(force)
    moment = -(np.asarray(screw_torque)-np.asarray(tool_torque))
    moment += np.cross(np.asarray(screw_position)-np.asarray(support_position), reaction)
    return reaction, moment


class ScrewRuntime:
    def __init__(self, stage, state, physics_view, tool_path, screw_path):
        import torch
        from pxr import UsdPhysics

        self.torch, self.state = torch, state
        self.physics_view=physics_view
        self.support_path=None
        self.tool_path, self.screw_path = tool_path, screw_path
        prim = stage.GetPrimAtPath(screw_path)
        self.spec = json.loads(prim.GetAttribute('envcreator:fastenerSpec').Get())
        self.origin = np.array(self.spec['origin_world_m'], dtype=float)
        self.guide_joint = None
        if self.spec.get('guide_joint'):
            guide_path = screw_path.rsplit('/',1)[0] + '/' + self.spec['guide_joint'].rsplit('/',1)[-1]
            self.guide_joint = UsdPhysics.Joint(stage.GetPrimAtPath(guide_path))
            if not self.guide_joint:raise ValueError('Missing declared thread guide joint')
        support_path = self.spec.get('moving_support')
        self.support = None
        if support_path:
            prefix = screw_path.rsplit('/',1)[0]
            support_path = prefix + '/' + support_path.rsplit('/',1)[-1]
            self.support_path=support_path
            self.support = physics_view.create_rigid_body_view(support_path)
            if self.support.count != 1:
                raise ValueError('Moving support must bind one live rigid body')
            self.support_origin = np.array(self.spec['origin_support_local_m'])
        if self.spec['axis_world'] != [0, 0, 1]:
            raise ValueError('This thread implementation currently supports a world +Z screw axis')
        thread_parameters = dict(self.spec.get('thread_parameters', {}))
        thread_parameters.update({k: self.spec[k] for k in ('lead_m', 'engagement_m', 'separation_m')})
        self.model = ScrewModel(Parameters(**thread_parameters))
        hex_parameters = dict(self.spec.get('engagement_parameters', {}))
        hex_parameters.update({k: self.spec[k] for k in ('socket_af_m', 'tip_af_m')})
        self.hex = HexEngagement(EngagementParameters(**hex_parameters))
        if self.spec.get('solver') == 'native-d6-compliant-v1':
            raise ValueError('Experimental D6 helical model failed validation and is not supported')
        self.native_thread = self.spec.get('solver') == 'native-mimic-helical-v1'
        self.solver_thread = SolverThread(stage,self.spec,screw_path.rsplit('/',1)[0]) if self.native_thread else None
        self.implicit = self.native_thread or self.spec.get('solver') == 'coupled-backward-euler-v1'
        self.mass = float(UsdPhysics.MassAPI(prim).GetMassAttr().Get())
        mass_api=UsdPhysics.MassAPI(prim)
        diagonal=np.array(mass_api.GetDiagonalInertiaAttr().Get())
        axes=mass_api.GetPrincipalAxesAttr().Get(); aq=np.array([axes.GetReal(),*axes.GetImaginary()])
        rotation=np.column_stack([rotate(aq,np.eye(3)[i]) for i in range(3)])
        self.screw_inertia=np.diag(rotation @ np.diag(diagonal) @ rotation.T)
        self.screw_com_local=np.array(mass_api.GetCenterOfMassAttr().Get())
        tool_spec = json.loads(stage.GetPrimAtPath(tool_path).GetAttribute('envcreator:toolSpec').Get())
        self.tool = physics_view.create_articulation_view(tool_path + tool_spec.get('articulation_view_suffix',''))
        self.screw = physics_view.create_rigid_body_view(screw_path)
        if self.tool.count != 1 or self.screw.count != 1:
            raise ValueError('Mechanics requires unique live tool articulation and screw')
        self.names = list(self.tool.shared_metatype.link_names)
        self.spindle = self.names.index('spindle')
        spec = json.loads(stage.GetPrimAtPath(tool_path).GetAttribute('envcreator:toolSpec').Get())
        self.tcp = np.array(spec['frames']['tcp']['position_m'])
        self.dof = list(self.tool.shared_metatype.dof_names).index(spec['motor']['simulation_joint_name'])
        self.indices = torch.tensor([0], dtype=torch.int32)
        self.max_torque = float(spec['motor']['torque_limit_Nm'])
        self.motor_damping = float(spec['motor']['usd_velocity_damping'])
        self.max_rpm = float(spec['motor']['max_command_rpm'])
        self.passive_damping = float(spec['motor'].get('passive_damping_Nm_s_rad',0.))
        self.passive_limit = float(spec['motor'].get('passive_torque_limit_Nm',.01))
        rotor_prim = stage.GetPrimAtPath(tool_path + '/spindle')
        self.tool_mass=float(UsdPhysics.MassAPI(stage.GetPrimAtPath(tool_path+'/housing')).GetMassAttr().Get())+float(UsdPhysics.MassAPI(rotor_prim).GetMassAttr().Get())
        self.rotor_inertia = float(UsdPhysics.MassAPI(rotor_prim).GetDiagonalInertiaAttr().Get()[1])
        self.spindle_com_local = np.array(UsdPhysics.MassAPI(rotor_prim).GetCenterOfMassAttr().Get())
        self.housing = self.names.index('housing')
        damping = self.tool.get_dof_dampings().cpu().numpy().copy()
        stiffness = self.tool.get_dof_stiffnesses().cpu().numpy().copy()
        maximum = self.tool.get_dof_max_forces().cpu().numpy().copy()
        damping[0, self.dof] = 0. if self.implicit else self.motor_damping
        stiffness[0, self.dof] = 0.
        maximum[0, self.dof] = self.max_torque
        self.tool.set_dof_dampings(self.tensor(damping), self.indices)
        self.tool.set_dof_stiffnesses(self.tensor(stiffness), self.indices)
        self.tool.set_dof_max_forces(self.tensor(maximum), self.indices)
        self.stack_preload = self.native_thread and self.spec['solver_thread'].get('preload_application') == 'clamped_members'
        if self.stack_preload:
            from pxr import UsdUtils, PhysicsSchemaTools, Sdf
            self.stage_id=UsdUtils.StageCache.Get().GetId(stage).ToLongInt()
            self.lid_id=PhysicsSchemaTools.sdfPathToInt(Sdf.Path(screw_path.rsplit('/',1)[0]+'/lid'))
        if self.native_thread:
            from pxr import Gf
            self.stage=stage
            self.native_hex=UsdPhysics.Joint.Define(stage,screw_path+'_tool_coupling')
            self.native_hex.CreateBody0Rel().SetTargets([screw_path])
            self.native_hex.CreateBody1Rel().SetTargets([tool_path+'/spindle'])
            self.native_hex.CreateExcludeFromArticulationAttr(True)
            self.native_hex.CreateJointEnabledAttr(False)
            self.native_hex.CreateCollisionEnabledAttr(False)
            self.native_hex.CreateLocalPos1Attr(Gf.Vec3f(*map(float,self.tcp)))
            clearance=(self.hex.p.socket_af_m-self.hex.p.tip_af_m)/2
            for axis in ('transY','transZ'):
                limit=UsdPhysics.LimitAPI.Apply(self.native_hex.GetPrim(),axis)
                limit.CreateLowAttr(-clearance);limit.CreateHighAttr(clearance)
            bottom=UsdPhysics.LimitAPI.Apply(self.native_hex.GetPrim(),'transX')
            bottom.CreateLowAttr(-self.hex.p.socket_depth_m);bottom.CreateHighAttr(1.)
            # Geometric socket clearance permits a small tilt, not a weld.
            swing=math.degrees(math.atan2(clearance,self.hex.p.socket_depth_m))
            for axis in ('rotY','rotZ'):
                limit=UsdPhysics.LimitAPI.Apply(self.native_hex.GetPrim(),axis)
                limit.CreateLowAttr(-swing);limit.CreateHighAttr(swing)
            self.native_hex.CreateLocalRot0Attr(Gf.Quatf(math.sqrt(.5),Gf.Vec3f(0,-math.sqrt(.5),0)))
            drive=UsdPhysics.DriveAPI.Apply(self.native_hex.GetPrim(),'rotX')
            drive.CreateTypeAttr('force');drive.CreateStiffnessAttr(self.hex.p.stiffness_Nm_rad*math.pi/180)
            drive.CreateDampingAttr(self.hex.p.damping_Nm_s_rad*math.pi/180)
            drive.CreateMaxForceAttr(self.hex.p.torque_limit_Nm);drive.CreateTargetPositionAttr(0.)
            self.native_hex_active=False
            import omni.physx
            omni.physx.get_physx_simulation_interface().flush_changes()
        self.magnet = False
        self.magnet_offset = None
        self.steps, self.time_s = 0, 0.0
        self.report = {'available': True, 'motor': {'enabled': False, 'rpm': 0}, 'thread_state': 'clamped'}
        # Contact is replaced by the declared geometry-gated reciprocal coupling.
        for name in ('housing', 'spindle'):
            UsdPhysics.FilteredPairsAPI.Apply(stage.GetPrimAtPath(tool_path + '/' + name)).CreateFilteredPairsRel().AddTarget(prim.GetPath())
        state.tool_status = self.report
        state.tool_available = True
        state.event('tool-mechanics-bound', tool=tool_path, screw=screw_path,
                    timestep='each native physics substep', magneticRetention='capped 3N spring; geometric engagement required',
                    robot_grasp=False, pose_writes=False, solver=self.spec.get('solver', 'legacy-force-only'),
                    threadParameters=vars(self.model.p), engagementParameters=vars(self.hex.p),
                    motorTorqueLimitNm=self.max_torque, motorVelocityDamping=self.motor_damping)

    @staticmethod
    def pose(row):
        return np.array(row[:3]), np.array([row[6], row[3], row[4], row[5]])

    def tensor(self, value):
        return self.torch.tensor(np.asarray(value), dtype=self.torch.float32)

    def before_step(self, dt, *, defer_tool=False):
        self.steps += 1
        self.time_s += dt
        with self.state.lock:
            command = dict(self.state.tool_command)
            if self.state.clock() >= self.state.tool_deadline:
                self.state.tool_command['enabled'] = False
                self.state.tool_command['rpm'] = 0.0
                command = dict(self.state.tool_command)
            if self.implicit:
                command['rpm'] = float(np.clip(command['rpm'], -self.max_rpm, self.max_rpm))
            if command.get('release', False):
                self.magnet = False
                self.magnet_offset = None
        transform = self.tool.get_link_transforms()[0].cpu().numpy()
        velocity = self.tool.get_link_velocities()[0].cpu().numpy()
        position, q = self.pose(transform[self.spindle])
        screw_p, screw_q = self.pose(self.screw.get_transforms()[0].cpu().numpy())
        screw_v = self.screw.get_velocities()[0].cpu().numpy()
        screw_com=screw_p+rotate(screw_q,self.screw_com_local)
        screw_v[:3]+=np.cross(screw_v[3:],screw_p-screw_com)
        tcp = position + rotate(q, self.tcp)
        spindle_com = position + rotate(q,self.spindle_com_local)
        tcp_v = velocity[self.spindle, :3] + np.cross(velocity[self.spindle, 3:], tcp-spindle_com)
        support_p, support_q = np.zeros(3), np.array([1.,0.,0.,0.])
        local_position, local_velocity, local_angular = screw_p-self.origin, screw_v[:3], screw_v[3:]
        local_q = screw_q
        support_velocity = np.zeros(6)
        if self.support is not None:
            support_p, support_q = self.pose(self.support.get_transforms()[0].cpu().numpy())
            support_velocity = self.support.get_velocities()[0].cpu().numpy()
            support_com = support_p + rotate(support_q,self.support.get_coms()[0].cpu().numpy()[:3])
            inverse_q = support_q * np.array([1,-1,-1,-1])
            local_position = rotate(inverse_q, screw_p-support_p)-self.support_origin
            local_velocity = rotate(inverse_q, screw_v[:3]-support_velocity[:3]
                                    -np.cross(support_velocity[3:],screw_p-support_com))
            local_angular = rotate(inverse_q, screw_v[3:]-support_velocity[3:])
            local_q = multiply_q(inverse_q,screw_q)
        normal_frame = rotate(support_q,(0,0,1))
        event = self.model.observe(local_q, float(local_position[2]))
        self.model.native_twist=2*math.atan2(float(local_q[3]),float(local_q[0]))
        contact = self.hex.step(tcp, q, velocity[self.spindle, 3:], screw_p, screw_q, screw_v[3:])
        if event:
            if self.guide_joint and not self.native_thread and not self.model.thread_active:
                self.guide_joint.GetJointEnabledAttr().Set(False)
            self.state.event('fastener-transition', **event, physicsStep=self.steps)
        if contact['event']:
            self.state.event('hex-engagement', transition=contact['event'], physicsStep=self.steps, geometry=contact)
        if self.native_thread and contact['engaged'] != self.native_hex_active:
            from pxr import Gf
            if contact['engaged']:
                local=multiply_q(multiply_q(q*np.array([1,-1,-1,-1]),screw_q),np.array([math.sqrt(.5),0,-math.sqrt(.5),0]))
                self.native_hex.CreateLocalRot1Attr(Gf.Quatf(float(local[0]),Gf.Vec3f(*local[1:])))
            self.native_hex.GetJointEnabledAttr().Set(contact['engaged'])
            self.native_hex_active=contact['engaged']
            import omni.physx
            omni.physx.get_physx_simulation_interface().flush_changes()

        normal = rotate(screw_q,(0,0,1))
        axial = {'normal_force_N':0.,'surface':'disabled','penetration_m':0.}
        contact_mass=self.mass
        if self.native_thread:
            screw_axial_mass=self.mass+(self.screw_inertia[2]/(self.model.p.lead_m/math.tau)**2 if self.model.thread_active else 0.)
            contact_mass=1/(1/self.tool_mass+1/screw_axial_mass)
        if self.spec.get('axial_contact_enabled',False) and not (self.native_thread and contact['engaged']):
            face_velocity = screw_v[:3]
            relative_axial = float((tcp_v-face_velocity) @ normal)
            axial = axial_contact(contact,relative_axial,dt,contact_mass,
                                  socket_depth_m=self.hex.p.socket_depth_m)
        axial_on_screw = -normal*axial['normal_force_N']
        side_on_screw, side_moment = np.zeros(3), np.zeros(3)
        if self.spec.get('axial_contact_enabled',False) and not (self.native_thread and contact['engaged']):
            side_on_screw, side_moment=socket_contact_wrench(tcp,screw_p,-rotate(q,(0,1,0)),normal,
                tcp_v,screw_v[:3],velocity[self.spindle,3:],screw_v[3:],contact,dt,contact_mass,
                clearance_m=(self.hex.p.socket_af_m-self.hex.p.tip_af_m)/2)
        if self.native_thread and contact['engaged']:
            axial={'normal_force_N':None,'surface':'native_socket_bottom',
                   'penetration_m':max(0.,contact['insertion_m']-self.hex.p.socket_depth_m),
                   'model':'native unilateral socket bottom and bounded side clearance; actual joint reaction not sampled'}
        contact_force_on_screw=axial_on_screw+side_on_screw
        force, torque, metrics = self.model.forces(local_position, local_velocity, local_angular,
                                                  contact['transmitted_Nm'], 0.0)
        force, torque = np.array(force), np.array(torque)
        motor_torque_local = 0.
        if self.native_thread:
            was_released=self.solver_thread.released
            metrics=self.solver_thread.step(self.model,float(local_position[2]),dt)
            if self.solver_thread.released and not was_released:
                self.screw=self.physics_view.create_rigid_body_view(self.screw_path)
                self.support=self.physics_view.create_rigid_body_view(self.support_path)
            preload=metrics['preload_N']
            force=np.array([0.,0.,-preload]);torque=np.zeros(3)
            if self.stack_preload:
                from pxr import Gf
                import omni.physx
                # Equivalent internal bolt tension clamps the members directly.
                # Additive API preserves separately applied press/robot loads.
                clamp=-normal_frame*preload
                omni.physx.get_physx_simulation_interface().apply_force_at_pos(
                    self.stage_id,self.lid_id,Gf.Vec3f(*clamp),Gf.Vec3f(*map(float,screw_p)),'Force')
                force=np.zeros(3)
                torque=np.array([0.,0.,preload*self.model.p.lead_m/math.tau])
            # Hex torque is supplied by the native joint, not duplicated as an
            # external wrench; the reported value is a constitutive estimate.
            transmitted=contact['transmitted_Nm']
            contact['torque_measurement']='constitutive estimate; native joint solves actual impulse'
            metrics.update(transmitted_tool_torque_Nm=transmitted,motor_torque_Nm=None)
            contact['torque_to_screw_world_Nm']=[0.,0.,0.]
            contact['torque_to_tool_world_Nm']=[0.,0.,0.]
        elif self.implicit:
            axis = rotate(q, (0, 1, 0))
            sign = float(axis @ normal_frame)
            target_local = math.tau * command['rpm'] / 60 if command['enabled'] else 0.
            housing_omega = float((velocity[self.housing, 3:]-support_velocity[3:]) @ normal_frame)
            force, torque, metrics = implicit_screw_step(
                self.model, self.hex, dt=dt, xyz=local_position,
                velocity=local_velocity, angular_velocity=local_angular,
                tool_omega=float((velocity[self.spindle, 3:]-support_velocity[3:]) @ normal_frame),
                target_omega=housing_omega+target_local*sign,
                engaged=contact['engaged'], mass_kg=self.mass,
                inertia_z=float(self.screw_inertia[2]), rotor_inertia=self.rotor_inertia,
                motor_damping=self.motor_damping if command['enabled'] else self.passive_damping,
                motor_limit=self.max_torque if command['enabled'] else self.passive_limit, external_axial_N=-self.mass*9.81*normal_frame[2]+float(axial_on_screw @ normal_frame))
            transmitted = metrics['transmitted_tool_torque_Nm']
            normal = rotate(screw_q, (0,0,1))
            contact.update(transmitted_Nm=transmitted,
                           torque_to_screw_world_Nm=(normal*transmitted).tolist(),
                           torque_to_tool_world_Nm=(-normal*transmitted).tolist())
            motor_torque_local = metrics['motor_torque_Nm'] * sign
            if not contact['engaged']:
                # Away from the screw the spindle can have any world direction.
                # Integrate its actual revolute coordinate, not its world-Z component.
                relative_omega = float(self.tool.get_dof_velocities()[0, self.dof])
                c = self.motor_damping if command['enabled'] else self.passive_damping
                free_motor = c*(target_local-relative_omega)/(1+c*dt/self.rotor_inertia)
                limit = self.max_torque if command['enabled'] else self.passive_limit
                motor_torque_local = (passive_spindle_torque(relative_omega,dt,self.rotor_inertia,c,limit)
                    if not command['enabled'] and c>0 else float(np.clip(free_motor,-limit,limit)))
                metrics['motor_torque_Nm'] = motor_torque_local
        # Same tilt restraint as the ideal bench, against an anti-rotated support.
        if self.model.thread_active and not self.native_thread:
            w, x, y, z = local_q
            torque[0] += .03 * 2*(y*z-w*x) - .001*local_angular[0]
            torque[1] -= .03 * 2*(x*z+w*y) + .001*local_angular[1]
            if self.implicit:
                tilt_k, tilt_d = self.spec.get('tilt_stiffness_Nm_rad', .3), .001
                tilt_error = np.array([2*(y*z-w*x), -2*(x*z+w*y)])
                tilt_g = tilt_k*dt+tilt_d
                torque[:2] = (tilt_k*tilt_error-tilt_g*local_angular[:2]) / (1+tilt_g*dt/self.screw_inertia[:2])
        # Convert the local thread wrench to world coordinates. Hex torque must
        # remain equal and opposite in world coordinates, including small tilt.
        tau_local_hex = (0. if self.native_thread else metrics['transmitted_tool_torque_Nm']) * np.array([0.,0.,1.])
        force = rotate(support_q,force)
        torque = rotate(support_q,torque-tau_local_hex) + np.asarray(contact['torque_to_screw_world_Nm'])
        if self.support is not None:
            com = self.support.get_coms()[0].cpu().numpy()[:3]
            support_com = support_p + rotate(support_q,com)
            rf, rt = support_reaction(force,torque,contact['torque_to_screw_world_Nm'],screw_com,support_com)
            if self.stack_preload:
                rf+=normal_frame*preload
                rt+=np.cross(screw_p-support_com,normal_frame*preload)
            self.pending_support = (rf,rt)
            if not defer_tool:
                self.support.apply_forces_and_torques_at_position(self.tensor([rf]),self.tensor([rt]),None,self.indices,True)
            metrics['support_reaction_force_world_N'] = rf.tolist()
            metrics['support_reaction_torque_world_Nm'] = rt.tolist()
        if contact['engaged'] and not command.get('release', False) and not self.magnet:
            self.magnet = True
            self.magnet_offset = screw_p - tcp
            self.state.event('magnetic-retention-acquired', physicsStep=self.steps, maxForceN=3.0)
        if self.magnet and self.model.thread_active and contact['engaged']:
            # Store the actual final insertion before the connection releases;
            # initial shallow insertion is not the later magnetic rest offset.
            self.magnet_offset = screw_p-tcp
        magnetic_force = np.zeros(3)
        if self.magnet and not self.model.thread_active:
            delta = tcp + self.magnet_offset - screw_p
            if np.linalg.norm(delta) > .015:
                self.magnet = False
                self.state.event('magnetic-retention-lost', physicsStep=self.steps)
            else:
                magnetic_force = 1000 * delta + 3.0 * (tcp_v - screw_v[:3])
                norm = np.linalg.norm(magnetic_force)
                if norm > 3:
                    magnetic_force *= 3 / norm
        force += magnetic_force + contact_force_on_screw
        torque += side_moment + np.cross(screw_p-screw_com,contact_force_on_screw+magnetic_force)
        link_forces = np.zeros((1, len(self.names), 3), dtype=np.float32)
        link_torques = np.zeros_like(link_forces)
        link_forces[0, self.spindle] = -magnetic_force - contact_force_on_screw
        # Equal opposite contact wrench, transported from screw COM to spindle COM.
        link_torques[0, self.spindle] = (contact['torque_to_tool_world_Nm']
            - side_moment + np.cross(screw_p - spindle_com, -magnetic_force-contact_force_on_screw))
        target = math.tau * command['rpm'] / 60 if command['enabled'] else 0.0
        omega = float(self.tool.get_dof_velocities()[0, self.dof])
        # PhysX implicit velocity drive remains stable with the small rotor inertia.
        # Torque is limited by set_dof_max_forces; this sets a target, never velocity.
        target_array = self.tool.get_dof_velocity_targets().cpu().numpy().copy()
        target_array[0, self.dof] = target
        if self.native_thread:
            damping,maximum=motor_drive_parameters(self.tool.get_dof_dampings().cpu().numpy(),
                self.tool.get_dof_max_forces().cpu().numpy(),self.dof,enabled=command['enabled'],
                active_damping=self.motor_damping,passive_damping=self.passive_damping,
                active_limit=self.max_torque,passive_limit=self.passive_limit)
            if not defer_tool:
                self.tool.set_dof_actuation_forces(self.tensor(np.zeros_like(target_array)),self.indices)
                self.tool.set_dof_dampings(self.tensor(damping),self.indices)
                self.tool.set_dof_max_forces(self.tensor(maximum),self.indices)
                self.tool.set_dof_velocity_targets(self.tensor(target_array),self.indices)
            self.pending_native_motor=(damping,maximum,target_array)
        elif self.implicit:
            actuation = np.zeros_like(target_array)
            actuation[0, self.dof] = motor_torque_local
            if not defer_tool:
                self.tool.set_dof_actuation_forces(self.tensor(actuation), self.indices)
        else:
            self.tool.set_dof_velocity_targets(self.tensor(target_array), self.indices)
        if not defer_tool:
            self.tool.apply_forces_and_torques_at_position(self.tensor(link_forces), self.tensor(link_torques), None, self.indices, True)
        self.pending_tool = (link_forces,link_torques,motor_torque_local,contact['engaged'])
        self.screw.apply_forces_and_torques_at_position(self.tensor([force]), self.tensor([torque]), None, self.indices, True)
        if self.steps % 20 == 0:
            status = {'available': True, 'physics_step': self.steps, 'sim_time_s': self.time_s,
                      'motor': {'enabled': command['enabled'], 'command_rpm': command['rpm'],
                                'measured_rpm': omega*60/math.tau, 'torque_limit_Nm': self.max_torque, 'drive': 'PhysX native bounded velocity drive' if self.native_thread else 'coupled implicit torque solve' if self.implicit else 'PhysX implicit velocity drive',
                                'applied_command_id': command.get('command_id'),
                                'off_behavior':'passive bearing drag' if self.passive_damping else 'ideal frictionless coast',
                                'passive_damping_Nm_s_rad':self.passive_damping},
                      'tcp_world_m': tcp.tolist(), 'tool_axis_world': rotate(q, (0,1,0)).tolist(),
                      'screw_position_world_m': screw_p.tolist(), 'screw_rotation_rad': self.model.theta,
                      'screw_exit_m': float(local_position[2]), 'thread_state': self.model.state,
                      'thread_active': self.model.thread_active, 'magnetic_retention': self.magnet,
                      'magnetic_force_N': magnetic_force.tolist(), 'engagement': contact,
                      'axial_contact':axial, 'socket_side_force_world_N':side_on_screw.tolist(),
                      'socket_side_moment_world_Nm':side_moment.tolist(),
                      'thread_metrics': metrics, 'fixed_wrist_not_grasp': True,
                      'assumptions': 'native helical joint and equivalent preload, no resolved threads; 3N magnetic retention surrogate' if self.native_thread else 'ideal anti-rotated threaded fixture, no real threads; 3N magnetic retention surrogate'}
            with self.state.lock:
                self.state.tool_status = status
                self.report = status
            if self.steps % 100 == 0:
                self.state.event('tool-mechanics-sample', **status)


class AssemblyRuntime:
    """Three independent thread constraints, one shared tool and moving support.

    A single aggregation applies support/tool wrenches once per substep, avoiding
    PhysX overwrite of earlier fastener contributions. No robot policy here.
    """
    def __init__(self, stage, state, physics_view, tool_path, screw_paths):
        self.state=state
        self.screws=[ScrewRuntime(stage,state,physics_view,tool_path,path) for path in screw_paths]
        if not all(s.implicit and s.support is not None for s in self.screws):
            raise ValueError('Assembly requires implicit screws and a moving mating body')

    def before_step(self,dt):
        for screw in self.screws:
            screw.before_step(dt,defer_tool=True)
        first=self.screws[0]
        engaged=[s for s in self.screws if s.pending_tool[3]]
        # A physical bit must not simultaneously drive two distinct screw heads.
        if len(engaged)>1:
            raise RuntimeError('One bit cannot engage multiple fasteners')
        chosen=engaged[0] if engaged else min(self.screws,key=lambda s:s.report.get('engagement',{}).get('radial_error_m',float('inf')))
        lf=sum((s.pending_tool[0] for s in self.screws),np.zeros_like(first.pending_tool[0]))
        lt=sum((s.pending_tool[1] for s in self.screws),np.zeros_like(first.pending_tool[1]))
        actuation=np.zeros((1,first.tool.max_dofs),dtype=np.float32)
        actuation[0,first.dof]=chosen.pending_tool[2]
        if first.native_thread:
            damping,maximum,target=chosen.pending_native_motor
            first.tool.set_dof_dampings(first.tensor(damping),first.indices)
            first.tool.set_dof_max_forces(first.tensor(maximum),first.indices)
            first.tool.set_dof_velocity_targets(first.tensor(target),first.indices)
        first.tool.set_dof_actuation_forces(first.tensor(actuation),first.indices)
        first.tool.apply_forces_and_torques_at_position(first.tensor(lf),first.tensor(lt),None,first.indices,True)
        rf=sum((s.pending_support[0] for s in self.screws),np.zeros(3))
        rt=sum((s.pending_support[1] for s in self.screws),np.zeros(3))
        first.support.apply_forces_and_torques_at_position(first.tensor([rf]),first.tensor([rt]),None,first.indices,True)
        with self.state.lock:
            self.state.tool_status={**chosen.report,'selected_screw':chosen.screw_path,
                'fasteners':{s.screw_path:s.report for s in self.screws},
                'support_reaction_force_world_N':rf.tolist(),
                'support_reaction_torque_world_Nm':rt.tolist(),
                'assembly_model':'native helical constraints, equivalent preload and reciprocal tool wrenches' if first.native_thread else 'moving support, reciprocal thread/hex wrenches; no calibrated clamp preload'}
