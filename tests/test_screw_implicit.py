"""High-load screw solver: independent force integration and negative controls."""
import math
import numpy as np
import pytest
from roboenv_perception.tools.fastener import Parameters, ScrewModel, implicit_screw_step
from roboenv_perception.tools.screwdriving import EngagementParameters, HexEngagement


def run_case(*, engaged=True, limit=3., pull=0., rpm=8., seconds=90., dt=.001):
    p=Parameters(lead_m=.0007,engagement_m=.008,separation_m=.024,
                 breakaway_Nm=2.,running_Nm=.05,angular_damping=.002,
                 coupling_stiffness=1e6,coupling_damping=20.,
                 lateral_stiffness=2e5,lateral_damping=5.,preload_release_m=.0001)
    model=ScrewModel(p)
    coupling=HexEngagement(EngagementParameters(stiffness_Nm_rad=50.,damping_Nm_s_rad=.02,torque_limit_Nm=3.))
    mass, iz, it=.012,2e-6,3.6e-7
    position=np.zeros(3);velocity=np.zeros(3);omega=np.zeros(3);wt=0.;tool_angle=0.
    peak=0.;max_residual=0.
    for step in range(round(seconds/dt)):
        angle=model.theta
        coupling.twist=tool_angle-angle if engaged else 0.
        f,t,m=implicit_screw_step(model,coupling,dt=dt,xyz=position,velocity=velocity,
            angular_velocity=omega,tool_omega=wt,target_omega=rpm*math.tau/60,
            engaged=engaged,mass_kg=mass,inertia_z=iz,rotor_inertia=it,
            motor_damping=10.,motor_limit=limit,external_axial_N=pull)
        velocity += (f+np.array([0,0,pull]))*dt/mass
        omega += t*dt/iz
        wt += (m['motor_torque_Nm']-m['transmitted_tool_torque_Nm'])*dt/it
        position += velocity*dt
        tool_angle += wt*dt
        theta=angle+omega[2]*dt
        model.observe([math.cos(theta/2),0,0,math.sin(theta/2)],position[2])
        assert np.allclose([velocity[2],omega[2],wt],
            [m['predicted_axial_speed_m_s'],m['predicted_screw_speed_rad_s'],m['predicted_spindle_speed_rad_s']],atol=1e-7)
        assert np.isfinite([*position,*omega,wt]).all()
        peak=max(peak,abs(m['transmitted_tool_torque_Nm']))
        if model.thread_active:max_residual=max(max_residual,abs(m['coupling_residual_m']))
        if not model.thread_active:break
    return model,position,peak,max_residual


def test_high_load_normal_rotation_clears_extended_thread():
    model,xyz,peak,residual=run_case()
    assert not model.thread_active
    assert model.theta/math.tau >= .008/.0007-.03
    assert peak > 1.9
    assert residual < .00005


@pytest.mark.parametrize('kwargs',[{'limit':1.},{'engaged':False},{'rpm':0.},{'pull':20.,'rpm':0.}])
def test_no_false_release_below_breakaway_unengaged_motor_off_or_axial_pull(kwargs):
    model,xyz,peak,residual=run_case(seconds=1.,**kwargs)
    assert model.thread_active and abs(model.theta)<.001
    assert xyz[2] < .0001


def test_support_wrench_preserves_linear_and_angular_momentum_about_world_origin():
    from roboenv_perception.tools.native_screw_runtime import support_reaction
    f=np.array([2.,-3.,11.]); tau=np.array([.3,-.4,1.8]); tool=np.array([0.,0.,2.])
    ps=np.array([.12,-.08,.09]); pb=np.array([-.01,.02,.035])
    rf,rt=support_reaction(f,tau,tool,ps,pb)
    assert np.allclose(f+rf,0)
    assert np.allclose(np.cross(ps,f)+tau+np.cross(pb,rf)+rt-tool,0)
