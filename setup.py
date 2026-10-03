"""Optional C extension (SIMD scanner). A missing compiler only costs speed, not functionality."""

import sys

from setuptools import Extension, setup

setup(
    ext_modules=[
        Extension(
            "easydbms.core.simd._native",
            sources=["easydbms/core/simd/_native.c"],
            extra_compile_args=["/O2"] if sys.platform == "win32" else ["-O3"],
            optional=True,
        )
    ]
)
