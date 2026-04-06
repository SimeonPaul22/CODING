"""
Setup configuration for Clinical Physiology Calculator
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="clinical-physiology-calculator",
    version="1.0.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="A command-line tool for calculating key physiological metrics and health indicators",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/clinical-physiology-calculator",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Healthcare Industry",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Medical Science Apps.",
    ],
    python_requires=">=3.7",
    entry_points={
        "console_scripts": [
            "physiology-calc=home:main",
        ],
    },
    keywords="physiology health calculator BMI BMR heart-rate fitness",
    project_urls={
        "Bug Reports": "https://github.com/yourusername/clinical-physiology-calculator/issues",
        "Source": "https://github.com/yourusername/clinical-physiology-calculator",
        "Documentation": "https://github.com/yourusername/clinical-physiology-calculator/blob/main/README.md",
    },
)
