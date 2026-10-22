"""UI Prototype Manager application package."""

# Register the small preview bridge routes on the existing page-management router
# before app.main includes that router. The bridge module imports app.main lazily.
from app import preview_bridge as _preview_bridge  # noqa: F401,E402
