"""Which virtual host answered a request, by the name the estate calls it.

One container serves three sites, and which one answers depends on the Host header
alone. Everything downstream of a request record -- which document root a path resolves
under, which site a visit belongs to -- therefore needs that decision, and it must be
made in exactly one place: a reader that decides a document root one way and labels the
record another way is a reader that quietly reports one site's traffic as another's.

The names below are the ones in the server's configuration, nothing else. A request
whose Host is not among them is still served, because the first site in the
configuration answers anything unrecognised, but it is not labelled: "the site that
happens to be first" is not a fact about the visitor, and a label invented here would be
indistinguishable, downstream, from one that was observed.
"""

from __future__ import annotations

WWW = "www"
STATIC = "static"
DOCS = "docs"

# The first label of a name, mapped to the site whose configuration claims it.
BY_LABEL = {
    "www": WWW,
    "static": STATIC,
    "assets": STATIC,
    "docs": DOCS,
    "handbook": DOCS,
}

# Names the public site answers to that carry no such label: the container's own name
# on each network, used by the deployment and by the monitoring.
WWW_NAMES = frozenset({"infra-web", "web01"})


def normalise(header: str | None) -> str | None:
    """Lower-case the name, drop the port and any trailing dot."""
    if not header:
        return None
    value = str(header).strip().rstrip(".").lower()
    if value.startswith("[") and "]" in value:      # an address literal with a port
        value = value[1:value.index("]")]
    elif value.count(":") == 1:
        value = value.split(":", 1)[0]
    return value or None


def resolve(header: str | None, site_domain: str | None = None) -> str | None:
    """The site that answered, or None when the name designates none of them.

    ``site_domain`` is the estate's own domain, which the public site answers to
    without any label in front of it.
    """
    name = normalise(header)
    if not name:
        return None
    if name in WWW_NAMES:
        return WWW
    domain = normalise(site_domain)
    if domain and name == domain:
        return WWW
    return BY_LABEL.get(name.split(".", 1)[0])


def document_root(header: str | None, site_domain: str | None = None) -> str:
    """Which document root the server read the file from.

    Unlike :func:`resolve`, this always answers, because the server always served
    something: an unrecognised name falls to the first site in the configuration, which
    is the public one. The two functions differ on purpose -- a file's size has to be
    looked up under the root the bytes actually came from, while a record must not
    assert a site nobody named.
    """
    return resolve(header, site_domain) or WWW
