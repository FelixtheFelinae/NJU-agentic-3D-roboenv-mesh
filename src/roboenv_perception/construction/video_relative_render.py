"""Blender worker: render the actual exported URDF meshes, in relative width units."""

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def panel_uv(part, x, y, spec):
    # Match the original perspective-rectification target, including image Y flip.
    return (.5+x, y/spec['lid_length_m'] if part == 'lid' else 1-y/spec['depth_m'])


def main(root):
    import bpy
    from mathutils import Euler, Matrix, Vector

    work = root/'objects/asset-0002'
    spec = json.loads((work/'build-spec.json').read_text())
    result = root/'renders'
    result.mkdir(exist_ok=True)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    tree = ET.parse(work/'export/object.urdf').getroot()
    joint = tree.find('joint')
    joint_origin = joint.find('origin')
    joint_xyz = [float(v) for v in (joint_origin.get('xyz', '0 0 0') if joint_origin is not None else '0 0 0').split()]
    opening = spec['scene_opening_rad']
    placed = []
    for link in tree.findall('link'):
        for visual in link.findall('visual'):
            origin = visual.find('origin')
            xyz = [float(v) for v in (origin.get('xyz', '0 0 0') if origin is not None else '0 0 0').split()]
            rpy = [float(v) for v in (origin.get('rpy', '0 0 0') if origin is not None else '0 0 0').split()]
            mesh = visual.find('geometry/mesh')
            mesh_path = (work/'export'/mesh.get('filename')).resolve()
            if not mesh_path.is_relative_to((work/'export').resolve()):
                raise ValueError('Mesh outside exported candidate')
            before = set(bpy.data.objects)
            bpy.ops.wm.obj_import(filepath=str(mesh_path), forward_axis='Y', up_axis='Z')
            transform = Matrix.Translation(Vector(xyz)) @ Euler(rpy, 'XYZ').to_matrix().to_4x4()
            transform = transform @ Matrix.Diagonal(Vector([*[float(v) for v in mesh.get('scale', '1 1 1').split()], 1]))
            if link.get('name') == 'lid':
                transform = Matrix.Translation(Vector(joint_xyz)) @ Matrix.Rotation(opening, 4, 'X') @ transform
            for obj in set(bpy.data.objects)-before:
                if obj.type != 'MESH':
                    continue
                obj.matrix_world = transform
                material = bpy.data.materials.new(obj.name+'_video')
                material.use_nodes = True
                shader = material.node_tree.nodes.get('Principled BSDF')
                shader.inputs['Base Color'].default_value = (.52, .52, .52, 1)
                shader.inputs['Metallic'].default_value = .45
                shader.inputs['Roughness'].default_value = .4
                obj.data.materials.clear()
                obj.data.materials.append(material)
                # Project photograph onto the observable upper/front panel only.
                uv = obj.data.uv_layers.new(name='video_panel')
                for polygon in obj.data.polygons:
                    for loop in polygon.loop_indices:
                        co = transform @ obj.data.vertices[obj.data.loops[loop].vertex_index].co
                        if link.get('name') == 'lid':
                            co = Matrix.Rotation(-opening, 4, 'X') @ (co-Vector(joint_xyz))
                        uv.data[loop].uv = panel_uv(link.get('name'), co.x, co.y, spec)
                texture_name = 'screen' if link.get('name') == 'lid' else 'keyboard'
                texture = work/f'appearance/{texture_name}.png'
                if texture.exists():
                    projected = material.copy()
                    node = projected.node_tree.nodes.new('ShaderNodeTexImage')
                    node.image = bpy.data.images.load(str(texture))
                    projected.node_tree.links.new(node.outputs['Color'], projected.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
                    projected.node_tree.nodes.get('Principled BSDF').inputs['Metallic'].default_value = 0
                    obj.data.materials.append(projected)
                    for polygon in obj.data.polygons:
                        if (link.get('name') == 'base' and polygon.normal.z > .8) or (link.get('name') == 'lid' and polygon.normal.z < -.8):
                            polygon.material_index = 1
                placed.append(obj)
    points = [obj.matrix_world @ Vector(v) for obj in placed for v in obj.bound_box]
    low = Vector([min(v[i] for v in points) for i in range(3)])
    high = Vector([max(v[i] for v in points) for i in range(3)])
    center = (low+high)/2
    span = max(high-low)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = 960, 720
    scene.render.resolution_percentage = 100
    scene.world.color = (.5, .5, .5)
    scene.view_settings.view_transform = 'AgX'
    for i, offset in enumerate([(1, 1, 2), (-1, -.5, 1.5)]):
        lamp = bpy.data.lights.new(f'light{i}', 'AREA')
        lamp.energy, lamp.size = 150*span**2, 2*span
        obj = bpy.data.objects.new(lamp.name, lamp)
        bpy.context.collection.objects.link(obj)
        obj.location = center+Vector(offset)*span
        obj.rotation_euler = (center-obj.location).to_track_quat('-Z', 'Y').to_euler()
    camera = bpy.data.objects.new('view', bpy.data.cameras.new('view'))
    bpy.context.collection.objects.link(camera)
    scene.camera = camera
    camera.data.type, camera.data.ortho_scale = 'ORTHO', 1.65*span
    for name, offset in [('preview', (1, 1.8, 1.2)), ('front', (0, 2, .7)), ('side', (2, .2, .5))]:
        camera.location = center+Vector(offset)*span
        camera.rotation_euler = (center-camera.location).to_track_quat('-Z', 'Y').to_euler()
        scene.render.filepath = str(result/f'{name}.png')
        bpy.ops.render.render(write_still=True)
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    report = {'candidate_sha256': digest(work/'model.py'),
              'export_hashes': {str(p.relative_to(work/'export')): digest(p) for p in (work/'export').rglob('*') if p.is_file()},
              'appearance_hashes': {p.name: digest(p) for p in (work/'appearance').glob('*.png')},
              'units': 'relative width=1; no physical simulation', 'renderer': bpy.app.version_string,
              'previews': ['preview.png', 'front.png', 'side.png']}
    (result/'result.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main(Path(sys.argv[sys.argv.index('--')+1]).resolve())
