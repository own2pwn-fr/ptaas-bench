"""One module per protocol the canary answers on.

Each listener is responsible for exactly two things: keep the client happy long enough
that its side of the exchange completes, and hand every observation to the Recorder.
None of them interpret the token or talk to the collector directly.
"""
