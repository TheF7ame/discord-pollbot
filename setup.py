from setuptools import setup, find_packages

setup(
    name="discord-poll-bot",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "discord.py>=2.0.0",
        "sqlalchemy>=2.0.0",
        "alembic>=1.0.0",
        "asyncpg>=0.27.0",
        "pydantic>=1.0.0,<2.0.0",
        "python-dotenv>=0.19.0",
    ],
    python_requires=">=3.9",
) 