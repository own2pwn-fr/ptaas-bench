"""Publishing platform for the Northgate Review: reader site, studio and public API.

The package is laid out by concern rather than by HTTP verb, because most of what this
service does is shared between the reader site and the studio:

``settings``      effective configuration, entirely from the environment
``store``         MongoDB and Redis handles, created lazily and shared per process
``seed``          derives the whole corpus of content from ``DEPLOY_SEED``
``identity``      accounts, sessions, roles, password and token handling
``observability`` telemetry wiring, the runtime integrity monitor, worker pools
``routers/``      the HTTP surface
"""

__version__ = "3.8.1"
