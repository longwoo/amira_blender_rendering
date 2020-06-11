#!/usr/bin/env python

"""This script can be used to generate the workstation scenario datasets.

The script must be run in blender, for instance from the command line using:

    $ blender -b -P scripts/render_dataset.py -- other-params

Example:

    $ blender -b -P scripts/render_dataset.py -- --abr-path ~/amira/amira_blender_rendering

"""

import bpy
import sys
import os
import re
import argparse
import numpy as np
import random
import logging
from math import log, ceil


def _err_msg():
    return \
        """Error: Could not import amira_blender_rendering. Either install it as a package,
        or specify a valid path to its location with the --abr-path command line argument."""


def import_abr(path=None):
    """(Try to) import amira_blender_rendering.

    This function tries to import amira_blender_rendering, either from python's
    installed packages (if path=None), or from a path (str). The reason this
    import happens this way is that the script this function belongs to is run
    from within blender, which might not have access to pip-installed packages.
    In this case, we need to specify an explicit path and add it to python's
    search path.

    Args:
        path (str): None, or path to amira_blender_rendering.
    """
    # NOTE: this is essentially the same code as in scripts/phirm. changes here
    # should likely be reflected there
    global abr, WorkstationScenarios, WorkstationScenariosConfiguration, expandpath, get_logger
    global PandaTable, PandaTableConfiguration

    if path is None:
        try:
            import amira_blender_rendering as abr
        except ImportError:
            print(_err_msg())
            sys.exit(1)
    else:
        abr_path = os.path.expanduser(os.path.expandvars(path))
        if not os.path.exists(abr_path):
            print(_err_msg())
            sys.exit(1)
        sys.path.append(abr_path)
        try:
            import amira_blender_rendering as abr
        except ImportError:
            print(_err_msg())
            sys.exit(1)

    # import additional parts
    import amira_blender_rendering.dataset
    import amira_blender_rendering.utils.blender as blender_utils
    import amira_blender_rendering.scenes
    from amira_blender_rendering.utils.io import expandpath
    from amira_blender_rendering.scenes.workstationscenarios import WorkstationScenarios, \
        WorkstationScenariosConfiguration
    from amira_blender_rendering.scenes.pandatable import PandaTable, PandaTableConfiguration
    from amira_blender_rendering.utils.logging import get_logger


def get_argv():
    """Get argv after --"""
    try:
        # only arguments after --
        return sys.argv[sys.argv.index('--') + 1:]
    except ValueError:
        return []


def get_cmd_argparser():
    parser = argparse.ArgumentParser(
        description='Render dataset in blender', prog="blender -b -P " + __file__,
        add_help=False)

    parser.add_argument(
        '--config',
        default='config/workstation_scenario01_train.cfg',
        help='Path to configuration file')

    parser.add_argument(
        '--abr-path',
        default='~/amira/amira_blender_rendering/src',
        help='Path where amira_blender_rendering (abr) can be found')

    parser.add_argument(
        '--viewsphere',
        action='store_true',
        help='Generate Viewsphere instead of RenderedObjects dataset')

    parser.add_argument(
        '--list-scenes',
        action='store_true',
        help='Print list of scenes and exit')

    parser.add_argument(
        '--print-config',
        action="store_true",
        help='Print configuration and exit')

    parser.add_argument(
        '-h',
        '--help',
        action='store_true',
        help='Print this help message and exit')

    return parser


def get_scene_types():
    # TODO: this could/should be handled with some internal registration
    #       mechanism that 'knows' all scenarios
    from amira_blender_rendering.scenes.workstationscenarios import WorkstationScenarios, \
        WorkstationScenariosConfiguration
    from amira_blender_rendering.scenes.simpletoolcap import SimpleToolCap, SimpleToolCapConfiguration
    from amira_blender_rendering.scenes.pandatable import PandaTable, PandaTableConfiguration

    # each scenario consists of a name, and a tuple containing the scenario as
    # well as its configuration
    return {
        'SimpleToolCap':
            [SimpleToolCap, SimpleToolCapConfiguration],
        'WorkstationScenarios':
            [WorkstationScenarios, WorkstationScenariosConfiguration],
        'PandaTable':
            [PandaTable, PandaTableConfiguration],
    }


def determine_scene_type(config_file):
    """Determine the scene type given a configuration file."""
    # don't parse the entire ini file, only look for scene_type
    scene_type = None
    pattern = re.compile('^\s*scene_type\s*=\s*(.*)\s*$', re.IGNORECASE)
    with open(config_file) as f:
        for line in f:
            match = pattern.match(line)
            if match is not None:
                scene_type = match.group(1)
    if scene_type is None:
        raise RuntimeError("scene_type missing from configuration file. Is your config valid?")
    return scene_type


def main():
    # parse command arguments
    cmd_parser = get_cmd_argparser()
    cmd_args = cmd_parser.parse_known_args(args=get_argv())[0]  # need to parse to get aps and abr
    import_abr(cmd_args.abr_path)

    # pretty print available scenarios?
    scene_types = get_scene_types()
    if cmd_args.list_scenes:
        print("List of possible scenes:")
        for k, _ in scene_types.items():
            print(f"   {k}")
        sys.exit(0)

    # change all keys to lower-case
    scene_types = dict((k.lower(), v) for k, v in scene_types.items())

    # check scene_type in config
    scene_type_str = determine_scene_type(cmd_args.config)
    if scene_type_str.lower() not in scene_types:
        raise RuntimeError(f"Invalid configuration: Unknown scene_type {scene_type_str}")

    # instantiate configuration
    config = scene_types[scene_type_str.lower()][1]()

    # combine parsers and parse command line arguments
    parser = argparse.ArgumentParser(
        prog="blender -b -P " + __file__,
        parents=[cmd_parser] + config.get_argparsers(),
        add_help=False)
    argv = get_argv()
    args = parser.parse_args(args=argv)
    # show help only here, because this will include the help for the dataset
    # configuration
    if args.help:
        parser.print_help()
        sys.exit(0)

    # check if the configuration file exists
    configfile = expandpath(args.config, check_file=True)

    # parse configuration from file, and then update with arguments
    config.parse_file(configfile)
    config.parse_args(argv=argv)

    # instantiate the scene.
    # NOTE: we do not automatically create splitting configs anymore. You need
    #       to run the script twice, with two different configurations, to
    #       generate the split. This is significantly easier than internally
    #       maintaining split configurations.
    scene = scene_types[scene_type_str.lower()][0](config=config)
    # save the config early. In case something goes wrong during rendering, we
    # at least have the config + potentially some images
    scene.dump_config()

    # generate the dataset
    success = False
    if cmd_args.viewsphere:
        success = scene.generate_viewsphere_dataset()
    else:
        success = scene.generate_dataset()
    if not success:
        get_logger().error("Error while generating dataset")

    # tear down scene. should be handled by blender, but a scene might have
    # other things opened that it should close gracefully
    scene.teardown()


if __name__ == "__main__":
    main()
