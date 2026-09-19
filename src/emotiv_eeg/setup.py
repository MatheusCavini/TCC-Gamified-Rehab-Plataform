from setuptools import find_packages, setup

package_name = 'emotiv_eeg'

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
    maintainer='matheus',
    maintainer_email='matheus@todo.todo',
    description='EMOTIV Cortex EEG hardware adapter for the rehabilitation platform.',
    license='TODO: License declaration',
    entry_points={
        'console_scripts': [
            'emotiv_eeg_node = emotiv_eeg.emotiv_eeg_node:main',
        ],
    },
)
