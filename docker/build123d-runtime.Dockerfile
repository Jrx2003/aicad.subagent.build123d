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
    "build123d==0.8.0" \
    "anytree==2.13.0" \
    "ocp-gordon==0.1.18" \
    "ocpsvg==0.5.0" \
    "scipy==1.16.2" \
    "svgelements==1.9.6" \
    "svgpathtools==1.7.1" \
    "svgwrite==1.4.3" \
    "sympy==1.14.0" \
    "trianglesolver==1.2" \
    "webcolors==24.11.1"

RUN cat >/usr/local/lib/python3.11/site-packages/py_lib3mf.py <<'PY'
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


class _Dummy:
    pass


class Lib3MF:
    ModelUnit = _ModelUnit
    ObjectType = _ObjectType
    MeshObject = _Dummy
    MeshObjectIterator = _Dummy
    ComponentsObject = _Dummy
    Wrapper = _Dummy
PY

RUN printf '%s\n' '#!/bin/sh' 'set -eu' 'exec "$@"' >/usr/local/bin/_entrypoint.sh \
    && chmod +x /usr/local/bin/_entrypoint.sh

WORKDIR /app

RUN mkdir -p /app /output
