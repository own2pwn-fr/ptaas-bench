"""Out-of-band canary service for ptaas-bench.

A blind vulnerability leaves nothing to observe in the response: the proof that a
tool really exploited it is that *something else* moved. This service is that
something else. It owns ``BENCH_OOB_DOMAIN`` and listens on the five channels a
payload can plausibly reach it through -- DNS, HTTP, HTTPS, SMTP, LDAP -- and turns
every hit into an ``oob`` event on the collector, keyed by the canary token the
payload carried.

Design constraints that shaped the code:

* A listener must never be slowed down or broken by the collector. Events go into a
  bounded queue drained by a background thread; when the collector is down or slow
  the events are dropped and counted, and the listener never notices.
* Unknown tokens are recorded too. A callback carrying a token we never planted
  means the tool used its own collaborator domain and something resolved it through
  us; that is a signal about the tool, not noise.
* Protocol support is deliberately shallow. We answer the first round-trip of each
  protocol convincingly and log; we are not a mail server, a directory server, or a
  JNDI exploitation server. Each listener module documents its own limits.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
