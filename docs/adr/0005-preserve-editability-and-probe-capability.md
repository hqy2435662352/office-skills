# Preserve complex objects and probe capability before declaring failure

Existing Deck Update preserves untouched complex objects exactly and never silently flattens editable content. When a requested change touches a difficult object, the Agent first performs a minimal controlled attempt on a copy and verifies reopening, structure, rendering, editability, and function; only demonstrated failure or unsupported behavior may block that object or slide, after which other authorized work continues and the user chooses whether to preserve, flatten, or skip it.
