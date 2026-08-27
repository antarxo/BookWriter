__version__ = "0.8.3"

# Install the run-aware maps-first typography bridge before native_builder imports
# the canonical contract bridge. The base bridge remains the fallback for
# paragraph geometry, callout frames and dominant typography.
from .map_run_typography_bridge import install_as_contract_bridge as _install_map_run_typography_bridge

_install_map_run_typography_bridge()
del _install_map_run_typography_bridge
