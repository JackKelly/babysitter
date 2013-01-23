from setuptools import setup, find_packages

print find_packages()

setup(
    name = "babysitter",
    version = "0.1",
    packages = find_packages(),
    install_requires = [],
    author = "Jack Kelly",
    author_email = "jack-list@xlk.org.uk",
    description = "Monitor files and processes and email heart beat",
    license = "MIT",
    keywords = "python monitor",
    url = "https://github.com/JackKelly/babysitter",
    download_url = "https://github.com/JackKelly/babysitter/tarball/master#egg=babysitter-dev",
    long_description = open('README.md').read()
)
