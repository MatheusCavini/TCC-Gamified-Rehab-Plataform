from setuptools import find_packages, setup

package_name = 'pulmonary_device'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='matheus',
    maintainer_email='matheus@todo.todo',
    description='Serial adapter for a generic single-value pulmonary rehab device.',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'pulmonary_device_node = pulmonary_device.pulmonary_device_node:main',
        ],
    },
)
