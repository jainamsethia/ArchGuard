"""Route modules for the ArchGuard dashboard.

Each module exposes an ``APIRouter``; ``app.py`` mounts them in a fixed order
and then mounts StaticFiles at "/" last.

There used to be an import-ordering workaround here. Route modules decorated
the shared ``app`` object at import time, so registration order followed import
order: a route submodule imported before ``archguard.dashboard.app`` finished
left the submodule partially initialised while app.py ran to completion --
static mount included -- and the submodule's routes then registered *after* the
mount, which shadowed them and turned every affected endpoint into a 404 from
StaticFiles. Importing app.py from this file first was what held that off.

Routers cannot have that problem: they are values, and app.py decides when they
are mounted.
"""
