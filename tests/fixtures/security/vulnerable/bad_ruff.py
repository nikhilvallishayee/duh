"""Fixture with a deliberate Ruff S-rule violation."""

import subprocess

def run_user_command(user_input: str) -> None:
    # S602: subprocess with shell=True from user input
    subprocess.run(user_input, shell=True)
