from pathlib import Path
import logging

log = logging.getLogger(__name__)

def load_traits(traits_list: list[str]) -> str:
    """
    Dynamically loads atomic skill contents based on the assigned traits list.
    Concatenates them into a single string to be injected into the system prompt.
    """
    from .constants import TRAITS_ROOT
    
    if not traits_list:
        return ""
        
    parts = []
    for trait in traits_list:
        trait_path = TRAITS_ROOT / f"{trait}.md"
        if trait_path.exists() and trait_path.is_file():
            try:
                content = trait_path.read_text(encoding="utf-8")
                parts.append(f"=== [特征能力: {trait}] ===\n{content.strip()}")
            except Exception:
                log.exception("Failed to load trait file %s", trait_path)
        else:
            log.warning("Trait file not found: %s", trait_path)
            
    if not parts:
        return ""
        
    return "\n\n【动态挂载特征能力】\n" + "\n\n".join(parts)
