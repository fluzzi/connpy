from setuptools import setup, find_packages
import pathlib

VERSION = '2.0.5' 
DESCRIPTION = 'Conn is a SSH/Telnet connection manager and automation module'

here = pathlib.Path(__file__).parent.resolve()
LONG_DESCRIPTION = (here / "README.md").read_text(encoding="utf-8")

# Setting up
setup(
        name="conn", 
        version=VERSION,
        author="Federico Luzzi",
        author_email="<fluzzi@gmail.com>",
        description=DESCRIPTION,
        long_description=LONG_DESCRIPTION,
        long_description_content_type="text/markdown",
        url="https://github.com/fluzzi/connpy",
        packages=find_packages(),
        install_requires=["inquirer","pexpect","pycryptodome"], # add any additional packages that 
        keywords=['networking', 'automation', 'ssh', 'telnet', 'connection manager'],
        classifiers= [
            "Development Status :: 4 - Beta",
            "Topic :: System :: Networking",
            "Intended Audience :: Telecommunications Industry",
            "Programming Language :: Python :: 3",
            "Natural Language :: English",
            # "Operating System :: MacOS :: MacOS X",
            # "Operating System :: Microsoft :: Windows",
            "Operating System :: Unix"
        ],
        entry_points={
                            'console_scripts': [
                                'conn=conn.app:main',
                            ]
                    }
)
