"""Effective settings, read from the environment once per process.

Everything the service does differently between deployments is here, and nothing here
has a default that only makes sense on a developer's machine: the container is the
only place this runs, so the defaults are the container's.
"""

from __future__ import annotations

import functools
import hashlib
import ipaddress
import os
from dataclasses import dataclass, field


def _split(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _switch(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    site_name: str = "Northgate Review"
    site_domain: str = "northgatereview.com"
    deploy_seed: str = ""

    mongo_url: str = "mongodb://press-mongo:27017"
    mongo_db: str = "pressroom"
    redis_url: str = "redis://press-cache:6379/0"

    media_root: str = "/var/lib/pressroom/media"

    session_cookie: str = "ng_session"
    session_ttl_s: int = 60 * 60 * 12
    # HS256 passphrase for the session cookie. It has been in the deployment
    # configuration since the first release; rotating it signs every reader out.
    session_secret: str = "northgate"
    # The studio builds share links in the browser so the button is instant, so the
    # same key is compiled into the front-end bundle.
    preview_signing_key: str = "ng-preview"

    # The estate's management range. Diagnostics and the trace switch are meant to be
    # reachable only from it; the network policy that enforced that has not followed
    # this service onto its current network.
    ops_cidrs: tuple[str, ...] = ("10.60.0.0/22",)

    # Consulted in order by the plugin installer.
    plugin_indexes: tuple[str, ...] = (
        "https://pypi.org/simple",
        "https://packages.northgate-internal.net/simple",
    )
    plugin_namespace: str = "northgate-"

    # Wire agencies and syndication partners, used to label an imported picture.
    media_providers: tuple[str, ...] = (
        "images.wirefeed-north.example",
        "cdn.harbourpress.example",
    )
    embed_providers: tuple[str, ...] = (
        "www.wirefeed-north.example",
        "player.northcast.example",
        "maps.civicatlas.example",
    )

    outbound_timeout_s: float = 4.0
    # The matcher's budget for one query normalisation, added after an incident.
    search_match_budget_s: float = 2.0

    # Left over from the migration: the recovery endpoint answers with the delivery
    # record it queues, which is how the flow was checked before the mail worker
    # existed. The setting that hides it again has never been turned on here.
    recovery_echo_delivery: bool = True

    analytics_site_id: str = "ngr-web-01"

    _ops_networks: tuple = field(default=(), repr=False, compare=False)

    def from_ops_range(self, peer_ip: str | None) -> bool:
        """True when the socket peer sits in the estate's management range.

        The argument is the socket peer and nothing else: a forwarded header is a
        claim by the caller, and a decision taken on it is a decision taken by them.
        """
        if not peer_ip:
            return False
        try:
            address = ipaddress.ip_address(peer_ip.strip())
        except ValueError:
            return False
        for cidr in self.ops_cidrs:
            try:
                if address in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def derived(self, purpose: str, length: int = 32) -> str:
        """A per-deployment value bound to the seed, for things nobody types."""
        digest = hashlib.sha256(f"{self.deploy_seed}:{purpose}".encode()).hexdigest()
        return digest[:length]


# A masthead is two words, and which two is the one thing about a publication that
# nothing else can be derived from. Both come from the deployment seed unless the
# deployment names them, so two installations of this platform are two publications
# rather than two copies of one.
_PLACES = (
    "Northgate", "Harbourside", "Westmoor", "Kingsferry", "Ashcombe", "Stonebridge",
    "Millbank", "Fairhaven", "Redcliff", "Oakmere", "Thornbury", "Saltmarsh",
    "Greyfriars", "Blackwater", "Elmsworth", "Cranleigh",
)
_MASTHEADS = ("Review", "Chronicle", "Gazette", "Herald", "Courier", "Ledger",
              "Observer", "Argus")


def _publication(seed: str) -> tuple[str, str]:
    digest = int(hashlib.sha256(f"{seed}:masthead".encode()).hexdigest(), 16)
    place = _PLACES[digest % len(_PLACES)]
    masthead = _MASTHEADS[(digest // len(_PLACES)) % len(_MASTHEADS)]
    return f"{place} {masthead}", f"{place}{masthead}".lower() + ".com"


@functools.lru_cache(maxsize=1)
def settings() -> Settings:
    seed = os.environ.get("DEPLOY_SEED", "")
    default_name, default_domain = _publication(seed)
    base = Settings(
        site_name=os.environ.get("SITE_NAME") or default_name,
        site_domain=os.environ.get("SITE_DOMAIN") or default_domain,
        deploy_seed=seed,
        mongo_url=os.environ.get("MONGO_URL", "mongodb://press-mongo:27017"),
        mongo_db=os.environ.get("MONGO_DB", "pressroom"),
        redis_url=os.environ.get("REDIS_URL", "redis://press-cache:6379/0"),
        media_root=os.environ.get("MEDIA_ROOT", "/var/lib/pressroom/media"),
        ops_cidrs=_split("OPS_CIDRS", "10.60.0.0/22"),
        recovery_echo_delivery=_switch("RECOVERY_ECHO_DELIVERY", True),
        analytics_site_id=os.environ.get("ANALYTICS_SITE_ID", "ngr-web-01"),
    )
    # The two keys move with the deployment seed so that two installations do not
    # share them; both stay short, because both were chosen by hand.
    session_secret = os.environ.get("SESSION_SECRET") or _weak_passphrase(seed)
    preview_key = os.environ.get("PREVIEW_SIGNING_KEY") or (
        "ngp-" + hashlib.sha256(f"{seed}:preview".encode()).hexdigest()[:16]
    )
    indexes = _split("PLUGIN_INDEX_URLS") or base.plugin_indexes
    return Settings(
        **{
            **base.__dict__,
            "session_secret": session_secret,
            "preview_signing_key": preview_key,
            "plugin_indexes": tuple(indexes),
        }
    )


# The passphrase somebody typed into the compose file on the first afternoon of the
# project. It is picked per deployment so two installations do not share it, but it is
# still one word off a keyboard, which is the whole reason it keeps coming up in
# reviews and keeps not being changed.
_PASSPHRASES = (
    "sunshine",
    "letmein",
    "iloveyou",
    "trustno1",
    "qwerty123",
    "monkey123",
    "dragon1",
    "password1",
    "football",
    "starwars",
)


def _weak_passphrase(seed: str) -> str:
    index = int(hashlib.sha256(f"{seed}:session".encode()).hexdigest(), 16)
    return _PASSPHRASES[index % len(_PASSPHRASES)]
