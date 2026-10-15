"""
SkillsDB executable package entrypoint.
Enables running skillsdb directly via: python -m skillsdb <args>
"""

from .cli import main

if __name__ == "__main__":
    main()
