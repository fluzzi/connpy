#!/usr/bin/env python3
'''
'''
from .core import node,nodes
from .configfile import configfile
from .connapp import connapp
from pkg_resources import get_distribution

__all__ = ["node", "nodes", "configfile"]
__version__ = "2.0.10"
__author__ = "Federico Luzzi"
__pdoc__ = {
    'core': False,
    'connapp': False,
}
