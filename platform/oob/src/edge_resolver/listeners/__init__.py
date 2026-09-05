"""One module per protocol this node answers on.

Each listener has exactly two responsibilities: keep the client's side of the exchange
completing normally, and hand every observation to the Recorder. None of them interpret
identifiers, decide attribution or talk to the reporting endpoint directly.
"""
