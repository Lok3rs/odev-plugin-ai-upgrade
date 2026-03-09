import setuptools

setuptools.setup(
    name="odev-plugin-ai-upgrade",
    version="1.0.0",
    author="Your Name",
    description="An AI-powered Odoo module upgrade tool using Litellm.",
    packages=setuptools.find_namespace_packages(include=["odev.plugins.*"]),
    python_requires=">=3.10",
)
