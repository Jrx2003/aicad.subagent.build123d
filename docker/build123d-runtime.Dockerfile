FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libegl1 \
    libfontconfig1 \
    libgl1 \
    libopengl0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip setuptools wheel

RUN python -m pip install \
    "anytree==2.13.0" \
    "cadquery-ocp==7.9.3.1" \
    "cadquery-ocp-proxy==7.9.3.1" \
    "ezdxf==1.4.3" \
    "ipython==8.39.0" \
    "numpy==2.4.4" \
    "scipy==1.17.1" \
    "svgelements==1.9.6" \
    "svgpathtools==1.7.2" \
    "svgwrite==1.4.3" \
    "sympy==1.14.0" \
    "typing_extensions==4.15.0" \
    "trianglesolver==1.2" \
    "webcolors==24.11.1"

# Linux aarch64 currently exposes cadquery-ocp 7.9.x wheels, while build123d 0.10.0
# still advertises a <7.9 metadata pin. Install the pure-Python layers without re-solving
# the OCP backend so the sandbox image stays buildable on Docker Desktop arm64.
RUN python -m pip install --no-deps \
    "build123d==0.10.0" \
    "ocp-gordon==0.2.0" \
    "ocpsvg==0.5.0"

# Linux arm64 does not currently expose a usable lib3mf wheel for this image.
# The runtime only needs `build123d` solid modeling and STEP/topology flows, not
# Mesher-backed 3MF I/O, so provide a narrow shim that keeps module importable
# and raises a clear error if 3MF export/import is actually invoked.
RUN cat >/usr/local/lib/python3.11/site-packages/lib3mf.py <<'PY'
class _ModelUnit:
    MicroMeter = 0
    MilliMeter = 1
    CentiMeter = 2
    Inch = 3
    Foot = 4
    Meter = 5


class _ObjectType:
    Other = 0
    Model = 1
    Support = 2
    SolidSupport = 3


class _UnsupportedLib3MF:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "lib3mf is unavailable in this sandbox image; 3MF import/export is unsupported."
        )


class _Dummy:
    pass


class Lib3MF:
    ModelUnit = _ModelUnit
    ObjectType = _ObjectType
    MeshObject = _Dummy
    MeshObjectIterator = _Dummy
    ComponentsObject = _Dummy
    Wrapper = _UnsupportedLib3MF


Lib3MF.__file__ = __file__
PY

RUN printf '%s\n' '#!/bin/sh' 'set -eu' 'exec "$@"' >/usr/local/bin/_entrypoint.sh \
    && chmod +x /usr/local/bin/_entrypoint.sh

WORKDIR /app

RUN mkdir -p /app /output
