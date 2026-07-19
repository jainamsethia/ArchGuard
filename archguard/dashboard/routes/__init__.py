# Route modules for the ArchGuard dashboard.
#
# Route modules register endpoints directly on the FastAPI app object with
# `@app.get(...)`, and app.py mounts the catch-all StaticFiles at "/" only
# AFTER importing every route module, so API routes always precede the mount
# in the route table (WEB-03).
#
# That ordering silently breaks if a route submodule is imported before
# archguard.dashboard.app: the circular import leaves the submodule partially
# initialized while app.py runs to completion (static mount included), and the
# submodule's routes then register AFTER the mount — which shadows them and
# turns every affected endpoint into a static-404.
#
# Importing app here first guarantees app.py is fully initialized (all routes
# registered, mount last) before any route submodule executes, regardless of
# what the consumer imports first. Python always executes this package
# __init__ before any submodule, so this covers direct submodule imports too.
import archguard.dashboard.app  # noqa: F401
