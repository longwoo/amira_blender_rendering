#!/usr/bin/env python

import bpy
import logging
from mathutils import Vector

def clean_orphaned_materials():
    """Remove all materials without user"""
    mats = []
    for mat in bpy.data.materials:
        if mat.users == 0:
            mats.append(mat)

    for mat in mats:
        mat.user_clear()
        bpy.data.materials.remove(mat)


def remove_material_nodes(obj: bpy.types.Object = bpy.context.object):
    """Remove all material nodes from an object"""
    obj.data.materials.clear()


def add_default_material(
    obj: bpy.types.Object = bpy.context.object,
    name: str = 'DefaultMaterial') -> bpy.types.Material:

    """Add a new 'default' Material to an object.

    This material will automatically create a Principled BSDF node as well as a Material Output node."""

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    return mat


def setup_material_metal_tool_cap(material: bpy.types.Material, empty: bpy.types.Object = None):
    """Setup material nodes for the metal tool cap"""
    # TODO: refactor into smaller node-creation functions that can be re-used elsewhere

    tree = material.node_tree
    nodes = tree.nodes

    # TODO: change this to some smarter logger function. here to be able to substitute quickly
    logger_warn = lambda x: print(f"WW: {x}")
    logger_info = lambda x: print(f"II: {x}")

    # check if default principles bsdf + metarial output exist
    if len(nodes) != 2:
        logger_warn("More shader nodes in material than expected!")

    # find if the material output node is available. If not, create it
    if 'Material Output' not in nodes:
        logger_warn("Node 'Material Output' not found in material node-tree")
        n_output = nodes.new('ShaderNodeOutputMaterial')
    else:
        n_output = nodes['Material Output']

    # find if the principled bsdf node is available. If not, create it
    if 'Principled BSDF' not in nodes:
        logger_warn("Node 'Principled BSDF' not found in material node-tree")
        n_bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    else:
        n_bsdf = nodes['Principled BSDF']

    # check if link from BSDF to output is available
    link_exists = False
    for l in tree.links:
        if (l.from_node == n_bsdf) and (l.to_node == n_output):
            link_exists = True
            break
    if not link_exists:
        tree.links.new(n_bsdf.outputs['BSDF'], n_output.inputs['Surface'])

    # set BSDF default values
    n_bsdf.inputs['Subsurface'].default_value = 0.6
    n_bsdf.inputs['Subsurface Color'].default_value = (0.8, 0.444, 0.444, 1.0)
    n_bsdf.inputs['Metallic'].default_value = 1.0

    # thin metallic surface lines (used primarily for normal/bump map computation)
    n_texcoord_bump = nodes.new('ShaderNodeTexCoord')
    # setup empty (reference for distance computations)
    if empty is None:
        # get currently selected object
        obj = bpy.context.object

        # add empty
        bpy.ops.object.empty_add(type='PLAIN_AXES')
        empty = bpy.context.object

        # locate at the top of the object (XXX: assumes obj is in default coordinates)
        v0 = Vector(obj.bound_box[1])
        v1 = Vector(obj.bound_box[2])
        v2 = Vector(obj.bound_box[5])
        v3 = Vector(obj.bound_box[6])
        empty.location = (v0 + v1 + v2 + v3) / 4

        # deselect all
        bpy.ops.object.select_all(action='DESELECT')

        # take care to re-select everything
        empty.select_set(state=True)
        obj.select_set(state=True)

        # make obj active again (will become parent of all selected objects)
        bpy.context.view_layer.objects.active = obj

        # make parent, keep transform
        bpy.ops.object.parent_set(type='OBJECT', xmirror=False, keep_transform=True)
    # set the empty as input for the texture
    n_texcoord_bump.object = empty

    # (dot)^2 (distance from empty)
    n_dot = nodes.new('ShaderNodeVectorMath')
    n_dot.operation = 'DOT_PRODUCT'
    tree.links.new(n_texcoord_bump.outputs['Object'], n_dot.inputs[0])
    tree.links.new(n_texcoord_bump.outputs['Object'], n_dot.inputs[1])

    n_pow = nodes.new('ShaderNodeMath')
    n_pow.operation = 'POWER'
    tree.links.new(n_dot.outputs[1], n_pow.inputs[0])

    # mapping input from empty to noise
    n_mapping = nodes.new('ShaderNodeMapping')
    tree.links.new(n_texcoord_bump.outputs['Object'], n_mapping.inputs[0])

    # generate and link up required noise textures
    n_noise0 = nodes.new('ShaderNodeTexNoise')
    n_noise0.inputs['Scale'].default_value = 1.0
    n_noise0.inputs['Detail'].default_value = 1.0
    n_noise0.inputs['Distortion'].default_value = 2.0
    tree.links.new(n_pow.outputs[0], n_noise0.inputs[0])

    n_noise1 = nodes.new('ShaderNodeTexNoise')
    n_noise1.inputs['Scale'].default_value = 300.0
    n_noise1.inputs['Detail'].default_value = 0.0
    n_noise1.inputs['Distortion'].default_value = 0.0
    tree.links.new(n_pow.outputs[0], n_noise1.inputs[0])

    # XXX: is this noise required?
    n_noise2 = nodes.new('ShaderNodeTexNoise')
    n_noise2.inputs['Scale'].default_value = 0.0
    n_noise2.inputs['Detail'].default_value = 0.0
    n_noise2.inputs['Distortion'].default_value = 0.1
    tree.links.new(n_mapping.outputs['Vector'], n_noise2.inputs[0])

    n_noise3 = nodes.new('ShaderNodeTexNoise')
    n_noise3.inputs['Scale'].default_value = 5.0
    n_noise3.inputs['Detail'].default_value = 2.0
    n_noise3.inputs['Distortion'].default_value = 0.0
    tree.links.new(n_mapping.outputs['Vector'], n_noise3.inputs[0])

    # color output
    n_colorramp_col = nodes.new('ShaderNodeValToRGB')
    n_colorramp_col.color_ramp.color_mode = 'RGB'
    n_colorramp_col.color_ramp.interpolation = 'LINEAR'
    n_colorramp_col.color_ramp.elements[0].position = 0.118
    n_colorramp_col.color_ramp.elements[1].position = 0.727
    tree.links.new(n_noise0.outputs['Fac'], n_colorramp_col.inputs['Fac'])

    n_output_color = nodes.new('ShaderNodeMixRGB')
    n_output_color.inputs['Fac'].default_value = 0.400
    n_output_color.inputs['Color1'].default_value = (0.485, 0.485, 0.485, 1.0)
    tree.links.new(n_colorramp_col.outputs['Color'], n_output_color.inputs['Color2'])

    # roughness finish
    n_mul_r = nodes.new('ShaderNodeMath')
    n_mul_r.operation = 'MULTIPLY'
    n_mul_r.inputs[1].default_value = 0.100
    tree.links.new(n_noise3.outputs['Fac'], n_mul_r.inputs[0])

    n_output_roughness = nodes.new('ShaderNodeMath')
    n_output_roughness.operation = 'ADD'
    n_output_roughness.inputs[1].default_value = 0.050
    tree.links.new(n_mul_r.outputs[0], n_output_roughness.inputs[0])

    # math nodes to mix noise with distance and get ring-effect (modulo), leading to bump map
    n_add0 = nodes.new('ShaderNodeMath')
    n_add0.operation = 'ADD'
    tree.links.new(n_pow.outputs[0], n_add0.inputs[0])
    tree.links.new(n_noise2.outputs['Fac'], n_add0.inputs[1])

    n_mul0 = nodes.new('ShaderNodeMath')
    n_mul0.operation = 'MULTIPLY'
    n_mul0.inputs[1].default_value = 300.000
    tree.links.new(n_add0.outputs[0], n_mul0.inputs[0])

    n_mod0 = nodes.new('ShaderNodeMath')
    n_mod0.operation = 'MODULO'
    n_mod0.inputs[1].default_value = 2.000
    tree.links.new(n_mul0.outputs[0], n_mod0.inputs[0])

    n_mul1 = nodes.new('ShaderNodeMath')
    n_mul1.operation = 'MULTIPLY'
    tree.links.new(n_noise1.outputs['Fac'], n_mul1.inputs[0])
    tree.links.new(n_mod0.outputs[0], n_mul1.inputs[1])

    n_min_n = nodes.new('ShaderNodeMath')
    n_min_n.operation = 'MINIMUM'
    tree.links.new(n_noise1.outputs['Fac'], n_min_n.inputs[0])
    tree.links.new(n_mul1.outputs[0], n_min_n.inputs[1])

    n_colorramp_rough = nodes.new('ShaderNodeValToRGB')
    n_colorramp_rough.color_ramp.color_mode = 'RGB'
    n_colorramp_rough.color_ramp.interpolation = 'LINEAR'
    n_colorramp_rough.color_ramp.elements[0].position = 0.159
    n_colorramp_rough.color_ramp.elements[1].position = 0.541
    tree.links.new(n_min_n.outputs[0], n_colorramp_rough.inputs[0])

    n_output_normal = nodes.new('ShaderNodeBump')
    n_output_normal.inputs['Strength'].default_value = 0.075
    n_output_normal.inputs['Distance'].default_value = 1.000
    tree.links.new(n_colorramp_rough.outputs['Color'], n_output_normal.inputs['Height'])

    # output nodes:
    #   n_output_color -> color / outputs['Color']
    #   n_output_roughness -> roughness / outputs['Value']
    #   n_output_normal -> normal / outputs['Normal']
    # hook to bsdf shader node
    tree.links.new(n_output_color.outputs['Color'], n_bsdf.inputs['Base Color'])
    tree.links.new(n_output_roughness.outputs['Value'], n_bsdf.inputs['Roughness'])
    tree.links.new(n_output_normal.outputs['Normal'], n_bsdf.inputs['Normal'])


def main():
    """First tear down any material assigned with the object, then create everything from scratch"""
    remove_material_nodes()
    clean_orphaned_materials()
    mat = add_default_material()
    setup_material_metal_tool_cap(mat)

if __name__ == "__main__":
    main()
