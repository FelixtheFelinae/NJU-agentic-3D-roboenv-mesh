"""Mechanical law checks; actual PhysX acceptance is a separate artifact."""
import math
import pytest
from roboenv_perception.tools.solver_thread import preload_force


def test_preload_releases_after_elastic_relaxation_not_after_whole_thread():
    lead,travel,force=.0007,.00004,2500.
    assert preload_force(0,lead,force,travel,2,1)==force
    half_angle=math.tau*travel/lead/2
    assert preload_force(half_angle,lead,force,travel,2,1)==pytest.approx(force/2)
    assert preload_force(half_angle*2,lead,force,travel,2,1)==pytest.approx(0,abs=1e-10)
    assert preload_force(math.tau*8/.7,lead,force,travel,2,1)==0


def test_preload_never_adds_force_after_unloading_and_initial_ramp_is_explicit():
    assert preload_force(0,.0007,2500,.00004,0,1)==0
    assert preload_force(0,.0007,2500,.00004,.5,1)==1250
    assert preload_force(-1,.0007,2500,.00004,2,1)==2500


def test_invalid_preload_parameters_rejected():
    with pytest.raises(ValueError):preload_force(0,.0007,2500,0,2,1)


def test_motor_off_preserves_loaded_carriage_drives():
    import numpy as np
    from roboenv_perception.tools.screwdriving import motor_drive_parameters
    damping=np.array([[80.,10.,80.,80.]])
    limits=np.array([[100.,3.,100.,100.]])
    new_d,new_l=motor_drive_parameters(damping,limits,1,enabled=False,
        active_damping=10.,passive_damping=5e-5,active_limit=3.,passive_limit=.01)
    assert np.array_equal(new_d[:,[0,2,3]],damping[:,[0,2,3]])
    assert np.array_equal(new_l[:,[0,2,3]],limits[:,[0,2,3]])
    assert new_d[0,1]==5e-5 and new_l[0,1]==.01
    assert damping[0,1]==10 and limits[0,1]==3
