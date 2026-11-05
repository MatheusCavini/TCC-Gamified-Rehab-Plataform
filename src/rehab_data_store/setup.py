from setuptools import find_packages, setup

package_name = 'rehab_data_store'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='TCC project',
    maintainer_email='tcc@example.invalid',
    description='ROS 2 session and biosignal persistence node',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'rehab_data_store = rehab_data_store.data_store_node:main',
        ],
    },
)
