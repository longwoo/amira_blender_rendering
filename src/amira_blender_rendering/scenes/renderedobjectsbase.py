"""This module specifies a base class for scenes that should be stored in the
RenderedObjects dataset format."""

import bpy
from mathutils import Vector

import os
from abc import ABC, abstractmethod
import numpy as np
import imageio
try:
    import ujson as json
except:
    import json

from amira_blender_rendering import camera_utils
from amira_blender_rendering import blender_utils as blnd
import amira_blender_rendering.nodes as abr_nodes
import amira_blender_rendering.scenes as abr_scenes
import amira_blender_rendering.math.geometry as abr_geom
from amira_blender_rendering.math.conversions import bu_to_mm

# import things from AMIRA Perception Subsystem that are required
from aps.core.interfaces import PoseRenderResult
from aps.core.cv.camera import boundingbox_from_mask


class RenderedObjectsBase(ABC, abr_scenes.BaseSceneManager):
    """This class contains functions that convert a scene to the RenderedObjects format."""


    def __init__(self, base_filename, dirinfo, K, width, height, unit_conversion=bu_to_mm):
        super(RenderedObjectsBase, self).__init__()
        self.obj = None

        self.reset()
        self.base_filename = base_filename
        self.dirinfo = dirinfo

        self.K = K
        self.width = width
        self.height = height
        self.unit_conversion = unit_conversion

        # setup blender scene, camera, object, and compositors.
        # Note that the compositor setup needs to come after setting up the objects

        self.setup_scene()
        self.setup_camera()
        self.setup_lighting()
        self.setup_object()
        self.setup_environment()
        self.setup_compositor()

    # the following abstract methods are the interface that a scene needs to
    # implement

    @abstractmethod
    def setup_scene(self):
        pass

    @abstractmethod
    def setup_object():
        pass

    @abstractmethod
    def setup_lighting():
        pass

    @abstractmethod
    def setup_environment():
        pass

    @abstractmethod
    def randomize():
        pass


    def postprocess(self):
        """Postprocessing the scene.

        This step will compute all the data that is relevant for
        PoseRenderResult. This data will then be saved to json. In addition,
        postprocessing will fix the filenames generated by blender.
        """

        # the compositor postprocessing takes care of fixing file names
        self.compositor.postprocess()

        # compute bounding boxes and save annotations
        corners2d = self.compute_2dbbox()
        aabb, oobb, corners3d =  self.compute_3dbbox()
        self.save_annotations(corners2d, corners3d, aabb, oobb)


    def setup_compositor(self):
        """Setup output compositor nodes"""
        self.compositor = abr_nodes.CompositorNodesOutputRenderedObject()

        # setup all path related information in the compositor
        # TODO: both in amira_deep_vision as well as here we actually only need
        # some schema that defines the layout of the dataset. This should be
        # extracted into an independent schema file. Note that this does not
        # mean to use any xml garbage! Rather, it should be as plain as
        # possible.
        self.compositor.setup(self.dirinfo, self.base_filename, objs=[self.obj], scene=bpy.context.scene)


    def set_base_filename(self, filename):
        if filename == self.base_filename:
            return
        self.base_filename = filename

        # update the compositor with the new filename
        self.compositor.update(
                self.dirinfo,
                self.base_filename,
                [self.obj])


    def convert_units(self, render_result):
        """Convert render_result units from blender units to target unit"""
        if self.unit_conversion is None:
            return render_result

        # convert all relevant units from blender units to target units
        result      = render_result
        result.t    = self.unit_conversion(result.t)
        result.aabb = self.unit_conversion(result.aabb)
        result.oobb = self.unit_conversion(result.oobb)

        return result


    def save_annotations(self, corners2d, corners3d, aabb, oobb):
        """Save annotations of a render result."""

        # create a pose render result. leave image fields empty, they will
        # currenlty not go to the state dict. this is only here to make sure
        # that we actually get the state dict defined in pose render result
        t = np.asarray(abr_geom.get_relative_translation(self.obj, self.cam))
        R = np.asarray(abr_geom.get_relative_rotation(self.obj, self.cam).to_matrix())
        render_result = PoseRenderResult(self.obj.name, None, None, None, None, None, None,
                R, t, corners2d, corners3d, aabb, oobb)

        if not os.path.exists(self.dirinfo.annotations):
            os.mkdir(self.dirinfo.annotations)

        # convert to desired units
        render_result = self.convert_units(render_result)

        # build json name, dump data
        fname_json = f"{self.base_filename}.json"
        fname_json = os.path.join(self.dirinfo.annotations, f"{fname_json}")
        json_data = render_result.state_dict()
        with open(fname_json, 'w') as f:
            json.dump(json_data, f, indent=0)


    def compute_2dbbox(self):
        """Compute the 2D bounding box around an object.

        This simply loads the file from disk and gets the pixels. Unfortunately,
        it is not possible right now to work around this with using blender's
        viewer nodes. That is, using a viewer node attached to ID Mask nodes
        will store an image only to bpy.data.Images['Viewer Node'], depending on
        which node is currently selected in the node editor... I have yet to find a
        programmatic way that circumvents re-loading the file from disk"""

        # XXX: currently hardcoded for single object

        # this is a HxWx3 tensor (RGBA or RGB data)
        mask = imageio.imread(self.compositor.fname_masks[0])
        mask = np.sum(mask, axis=2)
        return boundingbox_from_mask(mask)


    def reorder_bbox(self, aabb, order=[1, 0, 2, 3, 5, 4, 6, 7]):
        """Reorder the vertices in an aab according to a certain permutation order."""

        if len(aabb) != 8:
            raise RuntimeError(f'Unexpected length of aabb (is {len(aabb)}, should be 8)')

        result = list()
        for i in range(8):
            result.append(aabb[order[i]])

        return result



    def compute_3dbbox(self):
        """Compute all 3D bounding boxes (axis aligned, object oriented, and the 3D corners

        Blender has the coordinates and bounding box in the following way.

        The world coordinate system has x pointing right, y pointing forward,
        z pointing upwards. Then, indexing with x/y/z, the bounding box
        corners are taken from the following axes:

          0:  -x/-y/-z
          1:  -x/-y/+z
          2:  -x/+y/+z
          3:  -x/+y/-z
          4:  +x/-y/-z
          5:  +x/-y/+z
          6:  +x/+y/+z
          7:  +x/+y/-z

        This differs from the order of the bounding box as it was used in
        OpenGL. Ignoring the first item (centroid), the following re-indexing is
        required to get it into the correct order: [1, 0, 2, 3, 5, 4, 6, 7].
        This will be done after getting the aabb from blender, using function
        reorder_bbox.

        TODO: probably, using numpy is not at all required, we could directly
              store to lists. have to decide if we want this or not
        """

        # 0. storage for numpy arrays.
        np_aabb = np.zeros((9, 3))
        np_oobb = np.zeros((9, 3))
        np_corners3d = np.zeros((9, 2))

        # 1. get centroid and bounding box of object in world coordinates by
        # applying the objects rotation matrix to the bounding box of the object

        # axis aligned (no object rotation)
        aabb = [Vector(v) for v in self.obj.bound_box]
        # compute centroid
        aa_centroid = aabb[0] + (aabb[6] - aabb[0]) / 2.0
        # copy aabb before reordering to have access to it later
        aabb_orig = aabb
        # fix order of aabb for RenderedObjects
        aabb = self.reorder_bbox(aabb)
        # convert to numpy
        np_aabb[0, :] = np.array((aa_centroid[0], aa_centroid[1], aa_centroid[2]))
        for i in range(8):
            np_aabb[i+1, :] = np.array((aabb[i][0], aabb[i][1], aabb[i][2]))

        # object aligned (that is, including object rotation)
        oobb = [self.obj.matrix_world @ v for v in aabb_orig]
        # compute oo centroid
        oo_centroid = oobb[0] + (oobb[6] - oobb[0]) / 2.0
        # fix order for rendered objects
        oobb = self.reorder_bbox(oobb)

        # convert to numpy
        np_oobb[0, :] = np.array((oo_centroid[0], oo_centroid[1], oo_centroid[2]))
        for i in range(8):
            np_oobb[i+1, :] = np.array((oobb[i][0], oobb[i][1], oobb[i][2]))

        # project centroid+vertices and convert to pixel coordinates
        corners3d = []
        prj = abr_geom.project_p3d(oo_centroid, self.cam)
        pix = abr_geom.p2d_to_pixel_coords(prj)
        corners3d.append(pix)
        np_corners3d[0, :] = np.array((corners3d[-1][0], corners3d[-1][1]))

        for i,v in enumerate(oobb):
            prj = abr_geom.project_p3d(v, self.cam)
            pix = abr_geom.p2d_to_pixel_coords(prj)
            corners3d.append(pix)
            np_corners3d[i+1, :] = np.array((corners3d[-1][0], corners3d[-1][1]))

        return np_aabb, np_oobb, np_corners3d


    def setup_camera(self):
        """Setup camera, and place at a default location"""

        # add camera, update with calibration data, and make it active for the scene
        bpy.ops.object.add(type='CAMERA', location=(0.66, -0.66, 0.5))
        self.cam = bpy.context.object
        if self.K is not None:
            print(f"II: Using camera calibration data")
            self.cam = camera_utils.opencv_to_blender(self.width, self.height, self.K, self.cam)
        bpy.context.scene.camera = self.cam

        # look at center
        blnd.look_at(self.cam, Vector((0.0, 0.0, 0.0)))

