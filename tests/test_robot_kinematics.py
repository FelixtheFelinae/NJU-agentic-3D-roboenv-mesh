import math

import numpy as np

from roboenv_perception.construction.robot_kinematics import RobotKinematics


def test_urdf_joint_origin_and_local_axis_follow_parent_rotation(tmp_path):
    # Independent analytic two-link example: rotate +X about +Z, then translate
    # along the resulting child's +X. Deliberately list the child joint first.
    path = tmp_path/'arm.urdf'
    path.write_text('''<robot name="test">
      <link name="base"/><link name="link"/><link name="tip"/>
      <joint name="end" type="fixed"><parent link="link"/><child link="tip"/><origin xyz="2 0 0"/></joint>
      <joint name="turn" type="revolute"><parent link="base"/><child link="link"/>
        <origin xyz="1 0 0"/><axis xyz="0 0 1"/><limit lower="-3.14" upper="3.14"/></joint>
    </robot>''')
    model = RobotKinematics(path, root_xyz=(0, 0, 0))
    result = model.forward({'turn': math.pi/2})
    np.testing.assert_allclose(result['tip'][:3, 3], [1, 2, 0], atol=1e-10)
    np.testing.assert_allclose(result['tip'][:3, 0], [0, 1, 0], atol=1e-10)
