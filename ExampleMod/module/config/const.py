from alasio.config.entry.mod_entry import ModEntry

# A view of mod_entry.yaml for the code of the mod. The framework reads the
# YAML directly and never imports this module: the declaration of the mod is
# module/config/_index/mod_entry.yaml, and editing the values here has no
# effect on it.
entry = ModEntry.find_mod_entry(__file__)
