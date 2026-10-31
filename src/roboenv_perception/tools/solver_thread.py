"""Native helical constraint and reduced preload law for an enclosure screw.

PhysX mimic couples a lead slider to a revolute screw within a floating
articulation. Bounded thread friction is a native joint drive. The preload is a reduced bolt/stack load released over a declared elastic
relaxation distance. The detachable asset applies equivalent clamp force to
lid/body and reciprocal energy-release torque to screw/body. Internal bolt
elastic stress is unresolved.
No runtime pose/velocity writes and no resolved thread surface are used.
"""
import math


def preload_force(theta, lead_m, initial_N, relaxation_m, elapsed_s, ramp_s):
    """Preassembled joint unloading; tightening beyond initial preload is out of scope."""
    if min(lead_m, initial_N, relaxation_m, ramp_s) <= 0:
        raise ValueError('positive preload model parameters required')
    unload = max(0., theta * lead_m / math.tau)
    return initial_N * max(0., 1.-unload/relaxation_m) * min(1., max(0.,elapsed_s/ramp_s))


class SolverThread:
    def __init__(self, stage, spec, prefix):
        from pxr import UsdPhysics, PhysxSchema
        self.stage,self.p,self.spec=stage,spec['solver_thread'],spec
        self.hinge_path=prefix+'/'+spec.get('friction_joint',spec['guide_joint']).rsplit('/',1)[-1]
        self.connector=UsdPhysics.Joint(stage.GetPrimAtPath(prefix+'/'+spec['guide_joint'].rsplit('/',1)[-1]))
        self.slide_path=prefix+'/'+spec['lead_joint'].rsplit('/',1)[-1]
        self.hinge=UsdPhysics.Joint(stage.GetPrimAtPath(self.hinge_path))
        self.friction=UsdPhysics.DriveAPI(self.hinge.GetPrim(),spec.get('friction_axis','angular'))
        self.mimic=PhysxSchema.PhysxMimicJointAPI(stage.GetPrimAtPath(self.slide_path),'rotX')
        if not all((self.hinge,self.friction,self.mimic)):
            raise ValueError('Native helical assembly is missing its declared joints')
        self.time=0.;self.released=False;self.friction_rest=0.

    def step(self,model,axial,dt):
        self.time+=dt
        p=self.p
        active=model.thread_active
        if not active and not self.released:
            from pxr import PhysxSchema
            # Called before PhysX step, outside its callback lock.
            self.connector.GetJointEnabledAttr().Set(False)
            self.friction.GetMaxForceAttr().Set(0.)
            if self.spec.get("friction_axis"):self.hinge.GetJointEnabledAttr().Set(False)
            import omni.physx
            omni.physx.get_physx_simulation_interface().flush_changes()
            self.released=True
        load=preload_force(model.theta,model.p.lead_m,p['pretension_N'],p['relaxation_m'],self.time,p['settling_ramp_s']) if active else 0.
        resistance=p['running_torque_Nm']+(p['thread_mu']*p['pitch_diameter_m']/(2*math.cos(math.pi/6))+p.get('bearing_mu',0.)*p.get('bearing_radius_m',0.))*load if active else 0.
        if active:
            self.friction.GetMaxForceAttr().Set(resistance)
            stiffness=p.get('friction_stiffness_Nm_rad',0.)
            if stiffness:
                # Elasto-plastic presliding friction. The native bounded spring
                # solves with contacts; the rest angle yields at the torque cap.
                elastic=max(-resistance/stiffness,min(resistance/stiffness,model.theta-self.friction_rest))
                self.friction_rest=model.theta-elastic
                target=(self.friction_rest+math.pi)%math.tau-math.pi
                self.friction.GetTargetPositionAttr().Set(math.degrees(target))
        return {'solver':'native-PhysX-mimic-helical-v1','coupling_residual_m':axial-model.theta*model.p.lead_m/math.tau,
                'preload_N':load,'resistance_limit_Nm':resistance,
                'preload_model':'equivalent preassembled bolt-stack relaxation; no resolved elastic stress',
                'native_joint_reactions':True,'coupling_force_N':None}
