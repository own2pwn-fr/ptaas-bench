"""Internal edge resolver and asset endpoint.

Two roles in one process, both of which an ordinary corporate network has:

* the recursive-looking DNS resolver for the application network. It answers every
  name it is asked about, in any zone, with its own address on the interface the
  client can reach, and forwards a short explicit list of infrastructure names to the
  real upstream resolver;
* the destination those answers point at: HTTP, HTTPS, SMTP and LDAP listeners that
  complete the first round-trip of each protocol and record what was asked for.

Together they make every outbound connection an application is talked into making
land here and be attributable, whatever hostname the request used. That matters
because the interesting requests are the ones aimed at a host we have never heard of:
an application that is made to fetch ``http://z9x2.example-collab.net/`` from a
parameter is doing something worth recording, and a network that simply failed that
lookup would record nothing at all.

Operating rules the code keeps to:

* Reporting never slows a listener. Observations go to a bounded queue drained by a
  background thread; when the reporting endpoint is slow or absent they are dropped
  and counted, and the listeners do not notice.
* Everything is recorded, including names we cannot attribute. An unattributable
  lookup still says which container made it and when.
* Nothing served on the wire identifies this service as anything other than an edge
  node: see the strings asserted in tests/test_appearance.py.
"""

__all__ = ["__version__"]

__version__ = "0.2.0"
