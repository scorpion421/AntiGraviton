"""
Search and discovery engine: FTS5 indexing, synonym synapses, and skill suggestions.
"""

from .fts import search, suggest_skills, get_skill, get_active_rules, get_rule, list_all
from .synonyms import expand_query_with_synonyms, init_synonyms

__all__ = [
    "search",
    "suggest_skills",
    "get_skill",
    "get_active_rules",
    "get_rule",
    "list_all",
    "expand_query_with_synonyms",
    "init_synonyms",
]
