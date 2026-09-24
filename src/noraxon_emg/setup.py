from setuptools import find_packages, setup


package_name = 'noraxon_emg'

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
    description='Noraxon Acquire COM adapter that publishes live raw EMG blocks.',
    license='TODO: License declaration',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [
            'noraxon_emg_node = noraxon_emg.noraxon_emg_node:main',
        ],
    },
)
