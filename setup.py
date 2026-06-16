#!/usr/bin/env python3
"""Legacy setup.py for compatibility with older pip versions.

For modern pip, pyproject.toml is used instead.
"""

from setuptools import setup, find_packages

setup(
    name="hermes-companion-server",
    version="0.2.0",
    description="Hermes Companion Server — HTTP shim for Hermes API + Kanban CLI",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Hermes Community",
    author_email="community@hermes-agent.nousresearch.com",
    license="MIT",
    python_requires=">=3.10",
    packages=find_packages(include=["server*"]),
    package_data={
        "": ["hermes-companion-user.service"],
    },
    install_requires=[
        "aiohttp>=3.9",
        "pyyaml>=6.0",
        "qrcode[pil]>=7.4",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21",
            "ruff>=0.1",
        ],
    },
    entry_points={
        "console_scripts": [
            "hermes-companion = server.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)