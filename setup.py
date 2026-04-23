[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.backends.legacy:build"

[project]
name            = "panda"
version         = "0.2.0"
description     = "Pardus Alternative Driver Administration"
readme          = "README.md"
license         = { text = "GPL-2.0-only" }
requires-python = ">=3.8"
keywords        = ["pardus", "driver", "gpu", "grub", "nvidia", "fglrx"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Intended Audience :: System Administrators",
    "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.8",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: System :: Hardware :: Hardware Drivers",
    "Topic :: System :: Systems Administration",
]

[project.urls]
Homepage   = "https://www.pardus.org.tr/"
Repository = "https://github.com/pardus/panda"

[project.scripts]#!/usr/bin/python
#-*- coding: utf-8 -*-

from distutils.core import setup

setup(name="panda",
    version="0.1.3",
    description="Python Modules for panda",
    license="GNU GPL2",
    url="http://www.pardus.org.tr/",
    py_modules = ["panda"],
    data_files = [
        ("/usr/libexec", ["panda-helper"]),
    ]
)
panda = "panda:main"

[tool.setuptools]
py-modules = ["panda"]

[tool.setuptools.data-files]
"/usr/libexec" = ["panda-helper"]
