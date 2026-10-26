"""Simple photograph-derived appearance; visual only, no geometry or physics edits."""

import json
import shutil


def apply_appearance(stage, root, work, destination, spec):
    import numpy as np
    from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt

    appearance = work/'appearance'
    if not appearance.exists():
        return
    shutil.copytree(appearance, destination/'appearance', dirs_exist_ok=True)

    def material(name, color=(.8, .8, .8), texture=None):
        path = str(root.GetPath())+'/Looks/'+name
        mat = UsdShade.Material.Define(stage, path)
        shader = UsdShade.Shader.Define(stage, path+'/Surface')
        shader.CreateIdAttr('UsdPreviewSurface')
        shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        shader.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(.6)
        if texture:
            uv = UsdShade.Shader.Define(stage, path+'/UV')
            uv.CreateIdAttr('UsdPrimvarReader_float2')
            uv.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('st')
            uv.CreateOutput('result', Sdf.ValueTypeNames.Float2)
            tex = UsdShade.Shader.Define(stage, path+'/Texture')
            tex.CreateIdAttr('UsdUVTexture')
            tex.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('./appearance/'+texture))
            tex.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set('sRGB')
            tex.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(), 'result')
            tex.CreateOutput('rgb', Sdf.ValueTypeNames.Float3)
            shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.ConnectableAPI(), 'rgb')
        shader.CreateOutput('surface', Sdf.ValueTypeNames.Token)
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
        return mat

    def quad(path, points, mat):
        mesh = UsdGeom.Mesh.Define(stage, path)
        mesh.CreatePointsAttr([Gf.Vec3f(*p) for p in points])
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreateDoubleSidedAttr(True)
        UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, 'vertex').Set(
            [Gf.Vec2f(1, 1), Gf.Vec2f(0, 1), Gf.Vec2f(0, 0), Gf.Vec2f(1, 0)])
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)

    basepath = str(root.GetPath())
    if spec['category'] == 'laptop':
        w, d, h = spec['width_m']/2, spec['depth_m'], spec['lid_length_m']
        quad(basepath+'/base/KeyboardAppearance', [[w, 0, .00015], [-w, 0, .00015],
             [-w, d, .00015], [w, d, .00015]], material('Keyboard', texture='keyboard.png'))
        quad(basepath+'/lid/ScreenAppearance', [[w, h, -.00015], [-w, h, -.00015],
             [-w, 0, -.00015], [w, 0, -.00015]], material('Screen', texture='screen.png'))
    elif spec['category'] == 'tablet_case':
        w, d, h = spec['dimensions_m']
        top_z = spec.get('appearance_top_z_m', h)
        top_material = material('TabletCaseTop', texture='tablet_top.png')
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh) or not prim.GetName().startswith('vis_'):
                continue
            mesh = UsdGeom.Mesh(prim)
            vertices = np.asarray(mesh.GetPointsAttr().Get())
            counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get())
            indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
            top_faces = []
            offset = 0
            for face_id, count in enumerate(counts):
                face = vertices[indices[offset:offset + count]]
                if np.all(np.abs(face[:, 2] - top_z) < 1e-4):
                    top_faces.append(face_id)
                offset += count
            if not top_faces:
                continue
            uv = np.column_stack((
                (vertices[:, 0] + w / 2) / w,
                .84 * (d / 2 - vertices[:, 1]) / d,
            )).astype(np.float32)
            UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                'st', Sdf.ValueTypeNames.TexCoord2fArray, 'vertex'
            ).Set(Vt.Vec2fArray.FromNumpy(uv))
            subset = UsdGeom.Subset.CreateGeomSubset(
                mesh, 'TopPhoto', UsdGeom.Tokens.face,
                Vt.IntArray.FromNumpy(np.asarray(top_faces, dtype=np.int32)), 'materialBind'
            )
            UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(top_material)
    elif spec['category'] == 'headphones' and spec.get('mesh_backend') != 'blender':
        shell = material('HeadphoneShell', (.27, .29, .29))
        top = material('HeadphoneCupTop', (.34, .36, .36))
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh) or not prim.GetName().startswith('vis_'):
                continue
            mesh = UsdGeom.Mesh(prim)
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(shell)
            vertices = np.asarray(mesh.GetPointsAttr().Get())
            counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get())
            indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
            top_faces = []
            offset = 0
            for face_id, count in enumerate(counts):
                face = vertices[indices[offset:offset + count]]
                if np.all(face[:, 2] > .0459):
                    top_faces.append(face_id)
                offset += count
            if top_faces:
                subset = UsdGeom.Subset.CreateGeomSubset(
                    mesh, 'CupTop', UsdGeom.Tokens.face,
                    Vt.IntArray.FromNumpy(np.asarray(top_faces, dtype=np.int32)), 'materialBind'
                )
                UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(top)
    elif spec['category'] == 'mouse':
        data = json.loads((appearance/'projection.json').read_text())
        transform, k = np.array(data['local_to_camera']), np.array(data['intrinsics'])
        mat = material('MousePhoto', texture='mouse.png')
        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Mesh) and prim.GetName().startswith('vis_'):
                mesh = UsdGeom.Mesh(prim)
                vertices = np.asarray(mesh.GetPointsAttr().Get())
                camera = vertices@transform[:3, :3].T+transform[:3, 3]
                xy = camera[:, :2]/camera[:, 2:]
                xy *= 1+data['radial_k1']*(xy*xy).sum(axis=1, keepdims=True)
                pixels = xy*k[[0, 1], [0, 1]]+k[:2, 2]
                box = np.array(data['crop_box'])
                uv = (pixels-box[:2])/(box[2:]-box[:2])
                uv[:, 1] = 1-uv[:, 1]
                UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, 'vertex').Set(Vt.Vec2fArray.FromNumpy(uv.astype(np.float32)))
                UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat)
    elif spec['category'] == 'bottle':
        cream = material('PET', (.76, .80, .77))
        white = material('Cap', (.94, .94, .90))
        green = material('NeckBand', (.025, .22, .16))
        height = spec['dimensions_m'][2]
        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Mesh) and prim.GetName().startswith('vis_'):
                mesh = UsdGeom.Mesh(prim)
                vertices = np.asarray(mesh.GetPointsAttr().Get())
                indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3)
                z = vertices[indices, 2].mean(axis=1)
                UsdShade.MaterialBindingAPI.Apply(prim).Bind(cream)
                for name, mask, mat in [('Cap', z > height*.92, white),
                                         ('Band', (z > height*.77) & (z <= height*.91), green)]:
                    subset = UsdGeom.Subset.CreateGeomSubset(mesh, name, UsdGeom.Tokens.face,
                        Vt.IntArray.FromNumpy(np.flatnonzero(mask).astype(np.int32)), 'materialBind')
                    UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(mat)
        params = spec['geometry_source']['parameters_provisional']
        factor = spec['dimensions_m'][2]/params[-1]
        half_y = params[4]*factor/2+.0002
        half_x = params[3]*factor*.30
        label = material('BottleLabel', texture='label.png')
        for sign, suffix in [(1, 'Front'), (-1, 'Back')]:
            quad(basepath+'/body/Label'+suffix, [[half_x, sign*half_y, height*.59],
                 [-half_x, sign*half_y, height*.59], [-half_x, sign*half_y, height*.07],
                 [half_x, sign*half_y, height*.07]], label)
