"""Trusted EnvCreator generator: triangular shell, pierced lid and tapped bosses."""
import math
import bpy
from mathutils import Vector
p=globals()['parameters'];export_mesh=globals()['export_mesh']
side,height,wall,lid_t=p['side_m'],p['height_m'],p['wall_m'],p['lid_m']
r=side/math.sqrt(3)
vertices=[(r*math.cos(math.pi/2+i*2*math.pi/3),r*math.sin(math.pi/2+i*2*math.pi/3)) for i in range(3)]

def prism(name,xy,lo,hi):
    vs=[(x,y,z) for z in (lo,hi) for x,y in xy];n=len(xy)
    faces=[tuple(reversed(range(n))),tuple(range(n,2*n))]
    faces += [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    m=bpy.data.meshes.new(name);m.from_pydata(vs,[],faces);m.update()
    o=bpy.data.objects.new(name,m);bpy.context.collection.objects.link(o)
    return o

def boolean(obj,cutter,operation):
    bpy.context.view_layer.objects.active=obj
    mod=obj.modifiers.new('functional_geometry','BOOLEAN');mod.operation=operation;mod.solver='EXACT';mod.object=cutter
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter,do_unlink=True)

def cylinder(name,xy,radius,lo,hi):
    return prism(name,[(xy[0]+radius*math.cos(i*math.tau/32),xy[1]+radius*math.sin(i*math.tau/32)) for i in range(32)],lo,hi)

body=prism('body',vertices,0,height)
# Triangle inradius r/2: offset each wall inward by exactly the wall thickness.
inner_scale=1-2*wall/r
boolean(body,prism('cavity',[(x*inner_scale,y*inner_scale) for x,y in vertices],wall,height+.01),'DIFFERENCE')
for i,xy in enumerate(p['holes_xy_m']):
    boolean(body,cylinder('boss',xy,.010,wall-.001,height),'UNION')
    # Open pilot passage, virtual thread along its upper 8 mm.
    boolean(body,cylinder('pilot',xy,.00165,height-.012,height+.01),'DIFFERENCE')
export_mesh('triangular_body',body,part='body',rgba=(.11,.22,.32,1))
lid=prism('lid',vertices,height,height+lid_t)
for xy in p['holes_xy_m']:
    boolean(lid,cylinder('clearance',xy,.00225,height-.01,height+lid_t+.01),'DIFFERENCE')
export_mesh('triangular_lid',lid,part='lid',rgba=(.55,.65,.71,1))
# Three isolated screw objects are authored by the USD assembly stage, using the
# validated matching socket geometry rather than decorative heads.
for i,xy in enumerate(p['holes_xy_m']):
    top=height+lid_t+.004
    head=cylinder('screw_head',xy,.004,top-.004,top)
    # Visual socket consistent with the USD screw geometry (3.1 mm AF).
    socket=prism('hex_socket',[(xy[0]+.0031/math.sqrt(3)*math.cos(j*math.tau/6),
                              xy[1]+.0031/math.sqrt(3)*math.sin(j*math.tau/6)) for j in range(6)],top-.0025,top+.002)
    boolean(head,socket,'DIFFERENCE')
    boolean(head,cylinder('screw_shaft',xy,.002,top-.020,top-.0039),'UNION')
    export_mesh(f'screw_{i}',head,part=f'screw_{i}',rgba=(.5,.53,.57,1),material_class='steel')
