from setuptools import find_packages
from setuptools import setup

setup(
    name='hal_interfaces',
    version='0.0.0',
    packages=find_packages(
        include=('hal_interfaces', 'hal_interfaces.*')),
)
