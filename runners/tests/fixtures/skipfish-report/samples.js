var mime_samples = [
  { 'mime': 'text/html', 'samples': [
    { 'url': 'http://legacy-web/', 'linked': 2, 'len': 7294, 'dir': '_m0/0' } ]
  }
];

var issue_samples = [
  { 'severity': 4, 'type': 50102, 'samples': [
    { 'url': 'http://legacy-web/cgi-bin/ping.cgi?host=localhost', 'extra': 'host', 'sid': '0', 'dir': '_i0/0' },
    { 'url': 'http://legacy-web/cgi-bin/trace.cgi?target=1', 'extra': 'target', 'sid': '0', 'dir': '_i0/1' } ]
  },
  { 'severity': 3, 'type': 40101, 'samples': [
    { 'url': 'http://legacy-web/search.php?term=x', 'extra': 'term', 'sid': '0', 'dir': '_i1/0' } ]
  },
  { 'severity': 1, 'type': 20101, 'samples': [
    { 'url': 'http://legacy-web/slow.php', 'extra': '', 'sid': '0', 'dir': '_i2/0' } ]
  },
  { 'severity': 2, 'type': 40401, 'samples': [
    { 'url': 'http://legacy-web/README.old', 'extra': 'It\'s a leftover', 'sid': '0', 'dir': '_i3/0' } ]
  },
  { 'severity': 0, 'type': 60101, 'samples': [
    { 'url': 'http://legacy-web/unknown-type', 'extra': '', 'sid': '0', 'dir': '_i4/0' } ]
  }
];
