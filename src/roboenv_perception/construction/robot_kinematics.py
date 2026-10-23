"""URDF-derived arm IK used to command actual robot joints, never object poses."""

import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation


def vector(text, default='0 0 0'):
    return np.fromstring(text or default, sep=' ')


class RobotKinematics:
    def __init__(self, urdf: Path, root_xyz=(0, 0, .77792370309)):
        xml = ET.parse(urdf).getroot()
        self.joints = []
        for element in xml.findall('joint'):
            origin = element.find('origin')
            pose = np.eye(4)
            if origin is not None:
                pose[:3, :3] = Rotation.from_euler('xyz', vector(origin.get('rpy'))).as_matrix()
                pose[:3, 3] = vector(origin.get('xyz'))
            axis = element.find('axis')
            limit = element.find('limit')
            self.joints.append({'name': element.get('name'), 'type': element.get('type'),
                'parent': element.find('parent').get('link'), 'child': element.find('child').get('link'),
                'origin': pose, 'axis': vector(axis.get('xyz')) if axis is not None else np.array([0, 0, 1]),
                'limits': (float(limit.get('lower', -3.14)), float(limit.get('upper', 3.14))) if limit is not None else (0, 0)})
        children = {j['child'] for j in self.joints}
        self.root_link = next(e.get('name') for e in xml.findall('link') if e.get('name') not in children)
        self.root_pose = np.eye(4)
        self.root_pose[:3, 3] = root_xyz
        self.by_name = {j['name']: j for j in self.joints}

    def forward(self, positions):
        frames = {self.root_link: self.root_pose.copy()}
        remaining = list(self.joints)
        while remaining:
            ready = [j for j in remaining if j['parent'] in frames]
            if not ready:
                raise ValueError('URDF graph is not a tree')
            for joint in ready:
                motion = np.eye(4)
                if joint['type'] in {'revolute', 'continuous'}:
                    motion[:3, :3] = Rotation.from_rotvec(joint['axis']*positions.get(joint['name'], 0)).as_matrix()
                frames[joint['child']] = frames[joint['parent']]@joint['origin']@motion
                remaining.remove(joint)
        return frames

    def solve_arm(self, side, target, orientation, tool_offset, positions, max_nfev=100, axis_only=False):
        names = [f'{side}_{suffix}_joint' for suffix in ['shoulder_pitch', 'shoulder_roll', 'shoulder_yaw',
                                                       'elbow', 'wrist_roll', 'wrist_pitch', 'wrist_yaw']]
        lo, hi = np.array([self.by_name[name]['limits'] for name in names]).T
        seed = np.clip([positions.get(name, 0) for name in names], lo+1e-5, hi-1e-5)

        def residual(q):
            pose = self.forward(positions | dict(zip(names, q)))[f'{side}_wrist_yaw_link']
            point = pose[:3, 3]+pose[:3, :3]@tool_offset
            error = (pose[:3, 0]-orientation[:, 0]) if axis_only else Rotation.from_matrix(orientation.T@pose[:3, :3]).as_rotvec()
            return np.r_[(point-target)*100, error*5, (q-seed)*.015]

        result = least_squares(residual, seed, bounds=(lo+1e-5, hi-1e-5), max_nfev=max_nfev,
                               ftol=1e-5, xtol=1e-5, gtol=1e-5)
        solved = positions | dict(zip(names, result.x))
        pose = self.forward(solved)[f'{side}_wrist_yaw_link']
        point = pose[:3, 3]+pose[:3, :3]@tool_offset
        error = Rotation.from_matrix(orientation.T@pose[:3, :3]).magnitude()
        return solved, {'position_error_m': float(np.linalg.norm(point-target)),
                        'orientation_error_deg': float(np.degrees(error)), 'reached': point.tolist()}


def standing_pose():
    q = {'waist_pitch_joint': .28}
    for side, sign in [('left', 1), ('right', -1)]:
        q.update({f'{side}_hip_pitch_joint': -.2, f'{side}_knee_joint': .42,
                  f'{side}_ankle_pitch_joint': -.22, f'{side}_shoulder_pitch_joint': .25,
                  f'{side}_shoulder_roll_joint': sign*.18, f'{side}_elbow_joint': .8})
    return q
