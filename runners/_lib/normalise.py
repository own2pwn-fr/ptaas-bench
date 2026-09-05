"""Native tool output -> normalised findings.

Every scanner speaks its own dialect, and the single hardest field to translate is
the CWE. The scoring engine compares the CWE a tool claimed against the CWE the
catalog planted, so a *wrong* CWE is worse than no CWE: it turns a true positive
into a false positive on one row and inflates the false-positive count on another.

Consequences, and they are the reason this module looks the way it does:

* The mapping lives in ``cwe_map.yaml``, not in code. It is data a reviewer can read
  in one sitting and disagree with, and each entry carries a ``source`` explaining
  why that CWE and not another.
* When nothing in the table matches, the finding is emitted with ``cwe: null`` and
  the raw identifier is recorded in the run's ``unmapped.json``. We never fall back
  to "the closest CWE".
* A rule that *deliberately* maps to null (a technology fingerprint, a scanner's
  hedge) is a decision someone signed with a `source`, so it is NOT added to
  unmapped.json. That file is the list of things nobody has looked at yet; padding
  it with settled cases is how it stops being read.
* Tools that publish their own CWE (ZAP, most nuclei templates) are trusted
  by default; the table only intervenes where the tool is known to be silent or to
  use a legacy/pillar CWE the catalog does not use.

All parsers are defensive by construction. A scanner that dies mid-run leaves a
truncated report, and losing the whole run because the last JSON line is half
written would be an own goal: unparsable records are skipped and counted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .findings import Finding, NormaliseResult, norm_confidence, norm_method, norm_severity

CWE_MAP_PATH = Path(__file__).with_name("cwe_map.yaml")

# "CWE-89", "cwe-89", "89", 89 -> 89. Anything else -> None.
_CWE_RE = re.compile(r"^(?:cwe[-_ :]?)?(\d+)$", re.IGNORECASE)


def parse_cwe(value: Any) -> int | None:
    """Coerce the many spellings of a CWE id to an int, or None.

    Tools disagree on the type as much as on the value: ZAP emits the id as a JSON
    string, nuclei as a lowercased list like ``["cwe-89"]``, SARIF as a rule tag.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            got = parse_cwe(item)
            if got is not None:
                return got
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    match = _CWE_RE.match(str(value).strip())
    if not match:
        return None
    number = int(match.group(1))
    return number if number > 0 else None


@dataclass(frozen=True)
class Rule:
    """One row of the mapping table."""

    key: str
    cwe: int | None
    source: str
    name: str | None = None
    severity: str | None = None
    pattern: re.Pattern[str] | None = None
    # True for outputs that are not claims about the target at all (a scanner
    # reporting its own fetch failures). Emitting them would inflate the tool's
    # false-positive count with things it never asserted.
    skip: bool = False


class CweTable:
    """The auditable mapping table, loaded once and shared by every driver."""

    def __init__(self, data: dict[str, Any]):
        self.raw = data
        self.version = data.get("version")
        self.out_of_catalog: dict[int, str] = {
            int(k): str(v) for k, v in (data.get("out_of_catalog") or {}).items()
        }
        self._tools: dict[str, dict[str, Any]] = data.get("tools") or {}
        self._rules: dict[str, dict[str, Rule]] = {}
        self._patterns: dict[str, list[Rule]] = {}
        for tool, cfg in self._tools.items():
            self._rules[tool] = {
                str(key): self._make_rule(str(key), value)
                for key, value in (cfg.get("rules") or {}).items()
            }
            self._patterns[tool] = [
                self._make_rule(entry["match"], entry, compile_pattern=True)
                for entry in (cfg.get("patterns") or [])
            ]

    @staticmethod
    def _make_rule(key: str, value: Any, *, compile_pattern: bool = False) -> Rule:
        if not isinstance(value, dict):
            raise ValueError(f"mapping entry {key!r} must be a mapping, got {type(value).__name__}")
        if "cwe" not in value:
            # Explicit is the whole point: a missing key is an omission, an explicit
            # `cwe: null` is a decision someone made and signed with a source.
            raise ValueError(f"mapping entry {key!r} has no 'cwe' key (use `cwe: null` for unknown)")
        cwe = value["cwe"]
        if cwe is not None:
            cwe = int(cwe)
        return Rule(
            key=key,
            cwe=cwe,
            source=str(value.get("source", "")),
            name=value.get("name"),
            severity=value.get("severity"),
            pattern=re.compile(value["match"], re.IGNORECASE) if compile_pattern else None,
            skip=bool(value.get("skip", False)),
        )

    @classmethod
    def load(cls, path: Path | None = None) -> CweTable:
        path = path or CWE_MAP_PATH
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")))

    def tool_cfg(self, tool: str) -> dict[str, Any]:
        return self._tools.get(tool) or {}

    def rules(self, tool: str) -> dict[str, Rule]:
        return self._rules.get(tool, {})

    def patterns(self, tool: str) -> list[Rule]:
        return self._patterns.get(tool, [])

    def lookup(self, tool: str, *keys: Any) -> Rule | None:
        """First exact rule matching any of ``keys`` (tried in order)."""
        table = self.rules(tool)
        for key in keys:
            if key is None:
                continue
            rule = table.get(str(key))
            if rule is not None:
                return rule
        return None

    def match(self, tool: str, text: str | None) -> Rule | None:
        """First regex rule matching ``text``. Order in the YAML is significant."""
        if not text:
            return None
        for rule in self.patterns(tool):
            if rule.pattern and rule.pattern.search(text):
                return rule
        return None

    def is_null_value(self, tool: str, value: Any) -> bool:
        """True when the tool's own CWE field is a documented "no CWE" sentinel."""
        nulls = self.tool_cfg(tool).get("null_values", [])
        return value in nulls or (isinstance(value, str) and value.strip() in {str(n) for n in nulls})

    def declared_unmapped(self, tool: str) -> set[str]:
        return {str(k) for k in (self.tool_cfg(tool).get("unmapped") or {})}


_DEFAULT_TABLE: CweTable | None = None


def default_table() -> CweTable:
    global _DEFAULT_TABLE
    if _DEFAULT_TABLE is None:
        _DEFAULT_TABLE = CweTable.load()
    return _DEFAULT_TABLE


# --------------------------------------------------------------------------------
# helpers shared by the parsers
# --------------------------------------------------------------------------------


def _dig(obj: Any, dotted: str) -> Any:
    """Fetch a nested key by dotted path, tolerating absent intermediates."""
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _clean(value: Any) -> str | None:
    """Normalise a scalar to a non-empty string, or None.

    Tools use the empty string where they mean "not applicable" (ZAP's ``param`` on a
    site-wide alert, wapiti's ``parameter`` on a URL-level finding). Keeping "" would
    make the scorer believe a parameter named "" was tested.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _param_from_url(url: str | None) -> str | None:
    """Last resort for tools that point at ``/x?id=payload`` without naming the param.

    Only used when the tool gives no parameter at all, and only when the query string
    has exactly one parameter: guessing between several would be a coin toss, and the
    scorer credits `exercise` per parameter.
    """
    if not url or "?" not in url:
        return None
    query = urlsplit(url).query
    parts = [p for p in query.split("&") if p]
    if len(parts) != 1:
        return None
    name = parts[0].split("=", 1)[0]
    return _clean(name)


def _iter_json_lines(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield (line number, object) for a JSONL file, skipping unparsable lines."""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for lineno, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # truncated tail of a killed scan; see module docstring
            if isinstance(obj, dict):
                yield lineno, obj


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None


def _unmapped(tool: str, key: str, name: str | None, sample: str | None = None) -> dict[str, Any]:
    return {"tool": tool, "key": key, "name": name, "sample": (sample or "")[:280]}


# --------------------------------------------------------------------------------
# ZAP -- traditional-json report
# --------------------------------------------------------------------------------


def normalise_zap(path: Path, table: CweTable | None = None, tool: str = "zap") -> NormaliseResult:
    """Parse a ZAP ``traditional-json`` (or ``-plus``) report.

    Shape: ``{"site": [{"@name": ..., "alerts": [{"pluginid", "alert"/"name",
    "riskcode", "confidence", "cweid", "instances": [{"uri", "method", "param",
    "attack", "evidence"}]}]}]}``. Numbers arrive as strings; that is ZAP's XML
    heritage showing through the JSON writer.

    One alert with N instances becomes N findings. That is deliberate: the scorer
    works per (url, param), and collapsing instances would silently discard the
    evidence that a tool found the same class on five different endpoints.
    """
    table = table or default_table()
    out = NormaliseResult()
    doc = _load_json(path)
    if not isinstance(doc, dict):
        return out

    sites = doc.get("site")
    if isinstance(sites, dict):  # single-site reports are sometimes an object
        sites = [sites]
    if not isinstance(sites, list):
        return out

    # riskcode is ZAP's own scale; the strings in `riskdesc` are localised, the codes
    # are not, so we key on the code.
    risk_by_code = {"0": "info", "1": "low", "2": "medium", "3": "high"}
    conf_by_code = {"0": "low", "1": "low", "2": "medium", "3": "high", "4": "confirmed"}

    for si, site in enumerate(sites):
        if not isinstance(site, dict):
            continue
        for ai, alert in enumerate(site.get("alerts") or []):
            if not isinstance(alert, dict):
                continue
            plugin = _clean(alert.get("pluginid"))
            alert_ref = _clean(alert.get("alertRef")) or plugin
            name = _clean(alert.get("alert")) or _clean(alert.get("name"))

            # A per-plugin override wins over ZAP's own cweid: it exists precisely for
            # the alerts where ZAP reports a pillar/legacy CWE the catalog never uses.
            rule = table.lookup(tool, alert_ref, plugin)
            if rule is not None and rule.skip:
                continue
            if rule is not None:
                cwe = rule.cwe
            else:
                own = alert.get("cweid")
                cwe = None if table.is_null_value(tool, own) else parse_cwe(own)
            if cwe is None:
                out.unmapped.append(
                    _unmapped(tool, str(alert_ref or plugin or "?"), name, str(alert.get("cweid")))
                )

            severity = risk_by_code.get(str(alert.get("riskcode")).strip()) or norm_severity(
                alert.get("riskdesc", "").split(" ")[0] if alert.get("riskdesc") else None
            )
            confidence = conf_by_code.get(str(alert.get("confidence")).strip()) or norm_confidence(
                alert.get("confidence")
            )

            instances = alert.get("instances")
            if not isinstance(instances, list) or not instances:
                # `traditional-json` without the -plus suffix still emits instances;
                # a report template that does not is handled rather than dropped.
                instances = [{"uri": _clean(site.get("@name"))}]
            for ii, inst in enumerate(instances):
                if not isinstance(inst, dict):
                    continue
                url = _clean(inst.get("uri"))
                param = _clean(inst.get("param")) or _param_from_url(url)
                out.findings.append(
                    Finding(
                        tool=tool,
                        url=url,
                        method=norm_method(inst.get("method")),
                        param=param,
                        cwe=cwe,
                        name=name,
                        severity=severity,
                        confidence=confidence,
                        raw_ref=f"{path.name}#site[{si}].alerts[{ai}].instances[{ii}]",
                    )
                )
    return out


# --------------------------------------------------------------------------------
# nuclei -- JSONL (-jsonl -o file)
# --------------------------------------------------------------------------------


def normalise_nuclei(
    path: Path, table: CweTable | None = None, tool: str = "nuclei"
) -> NormaliseResult:
    """Parse nuclei JSONL results.

    Resolution order for the CWE, most specific first:
      1. a per-template-id rule in the table (corrections and template families that
         carry no classification),
      2. the template's own ``info.classification.cwe-id`` (a list like
         ``["cwe-89"]``),
      3. a tag rule (``info.tags`` contains e.g. ``sqli``),
      4. null.

    The tag step is last because tags are a search facility, not a taxonomy: a
    template tagged ``xss,dast`` may be a technology fingerprint.
    """
    table = table or default_table()
    out = NormaliseResult()
    if not path.exists():
        return out

    tag_rules = table.rules(f"{tool}_tags")
    for lineno, rec in _iter_json_lines(path):
        template_id = _clean(rec.get("template-id")) or _clean(rec.get("templateID"))
        info = rec.get("info") if isinstance(rec.get("info"), dict) else {}
        name = _clean(info.get("name")) or template_id
        tags = info.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        rule = table.lookup(tool, template_id)
        if rule is not None and rule.skip:
            continue
        decided = rule is not None
        cwe = rule.cwe if rule is not None else None
        if rule is None:
            cwe = parse_cwe(_dig(info, "classification.cwe-id"))
            decided = cwe is not None
        if cwe is None and rule is None:
            for tag in tags:
                tag_rule = tag_rules.get(str(tag).lower())
                if tag_rule is not None:
                    # A tag mapped to null on purpose ("tech", "panel") settles the
                    # question: the template makes no weakness claim.
                    cwe, decided = tag_rule.cwe, True
                    if cwe is not None:
                        break
        if cwe is None and not decided:
            out.unmapped.append(
                _unmapped(tool, str(template_id or "?"), name, ",".join(str(t) for t in tags))
            )

        url = (
            _clean(rec.get("matched-at"))
            or _clean(rec.get("matched_at"))
            or _clean(rec.get("url"))
            or _clean(rec.get("host"))
        )
        # DAST/fuzzing templates name the injection point explicitly
        # (`fuzzing_parameter` / `fuzzing_method`, emitted only for fuzzing results).
        # Everything else has to be recovered from the matched URL.
        param = _clean(rec.get("fuzzing_parameter")) or _param_from_url(url)
        method = norm_method(rec.get("fuzzing_method")) or norm_method(_first_request_method(rec))

        out.findings.append(
            Finding(
                tool=tool,
                url=url,
                method=method,
                param=param,
                cwe=cwe,
                name=name,
                severity=norm_severity(info.get("severity")),
                confidence="high" if rec.get("matcher-status", True) else "medium",
                raw_ref=f"{path.name}#L{lineno + 1}",
            )
        )
    return out


def _first_request_method(rec: dict[str, Any]) -> str | None:
    """Recover the HTTP verb from the raw request nuclei echoes back.

    ``-include-rr`` puts the raw request in ``request``; its first token is the verb.
    Without it the type is all we have, and http templates default to GET.
    """
    raw = rec.get("request")
    if isinstance(raw, str) and raw:
        first = raw.strip().split(" ", 1)[0]
        got = norm_method(first)
        if got:
            return got
    if rec.get("type") == "http":
        return "GET"
    return None


# --------------------------------------------------------------------------------
# wapiti -- JSON report (-f json)
# --------------------------------------------------------------------------------


def normalise_wapiti(
    path: Path, table: CweTable | None = None, tool: str = "wapiti"
) -> NormaliseResult:
    """Parse a wapiti3 JSON report.

    Shape: ``{"vulnerabilities": {"<category>": [entry, ...]}, "anomalies": {...},
    "additionals": {...}, "classifications": {"<category>": {"desc", "sol", "ref",
    "wstg", "cwe"?}}}``.

    Only ``vulnerabilities`` produces findings. ``anomalies`` (500s, timeouts) and
    ``additionals`` (fingerprints, informational notes) are claims about the target's
    behaviour, not about a flaw, and counting them would inflate wapiti's
    false-positive rate for things it never claimed.

    The category name is the mapping key, because wapiti's own ``classifications``
    block is not guaranteed to carry a CWE across versions; when it does, it is used
    as a fallback for categories the table has not seen.
    """
    table = table or default_table()
    out = NormaliseResult()
    doc = _load_json(path)
    if not isinstance(doc, dict):
        return out

    classifications = doc.get("classifications") if isinstance(doc.get("classifications"), dict) else {}
    vulns = doc.get("vulnerabilities")
    if not isinstance(vulns, dict):
        return out

    for category, entries in vulns.items():
        if not isinstance(entries, list):
            continue
        rule = table.lookup(tool, category)
        if rule is not None and rule.skip:
            continue
        if rule is not None:
            cwe = rule.cwe
        else:
            # Wapiti publishes no CWE field at all (only WSTG codes). The one place a
            # CWE appears is inside the reference links of the category description,
            # e.g. "https://cwe.mitre.org/data/definitions/89.html". Reading it there
            # is a documented, checkable fallback, not a guess about the finding.
            cwe = parse_cwe(_dig(classifications, f"{category}.cwe")) or _wapiti_ref_cwe(
                classifications.get(category)
            )
        if cwe is None and rule is None and entries:
            out.unmapped.append(_unmapped(tool, str(category), str(category)))

        for ei, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            url = _clean(entry.get("path")) or _clean(entry.get("url"))
            out.findings.append(
                Finding(
                    tool=tool,
                    url=url,
                    method=norm_method(entry.get("method")) or "GET",
                    param=_clean(entry.get("parameter")) or _param_from_url(url),
                    cwe=cwe,
                    name=_clean(category),
                    # wapiti's `level` is 1/2/3 in the JSON report (low/medium/high in
                    # some versions); both spellings are handled.
                    severity=_wapiti_level(entry.get("level")),
                    confidence=None,  # wapiti publishes no confidence; inventing one would be a lie
                    raw_ref=f"{path.name}#vulnerabilities['{category}'][{ei}]",
                )
            )
    return out


# wapitiCore/language/vulnerability.py: INFO=0, LOW=1, MEDIUM=2, HIGH=3, CRITICAL=4.
_WAPITI_LEVELS = {"0": "info", "1": "low", "2": "medium", "3": "high", "4": "critical"}


def _wapiti_level(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return _WAPITI_LEVELS.get(text) or norm_severity(text)


_MITRE_CWE_RE = re.compile(r"cwe[.-]?mitre\.org/data/definitions/(\d+)|CWE[-\s:]?(\d+)", re.IGNORECASE)


def _wapiti_ref_cwe(classification: Any) -> int | None:
    """Pull a CWE id out of a wapiti category's reference links.

    ``ref`` maps a title to a URL; both may mention the CWE. Only accepted when the
    category has exactly one distinct CWE -- a category referencing several is
    ambiguous, and an ambiguous mapping is worse than none.
    """
    if not isinstance(classification, dict):
        return None
    refs = classification.get("ref")
    if not isinstance(refs, dict):
        return None
    found: set[int] = set()
    for title, url in refs.items():
        for text in (str(title), str(url)):
            for match in _MITRE_CWE_RE.finditer(text):
                number = match.group(1) or match.group(2)
                if number:
                    found.add(int(number))
    return found.pop() if len(found) == 1 else None


# --------------------------------------------------------------------------------
# nikto -- JSON and XML
# --------------------------------------------------------------------------------


def normalise_nikto(
    path: Path, table: CweTable | None = None, tool: str = "nikto"
) -> NormaliseResult:
    """Parse a nikto report (JSON or XML).

    Nikto is the hard case for CWE mapping: it emits prose. There is no CWE, no CVE
    for most items, and the plugin id (``id``/``OSVDB``) is not a stable taxonomy.
    So the table matches ordered regexes against the message text, and anything that
    matches nothing gets ``cwe: null``. That is a real limitation of nikto being
    benchmarked on CWE-keyed ground truth, and the report says so rather than hiding
    it behind a keyword guess.
    """
    table = table or default_table()
    if path.suffix.lower() == ".xml" or _looks_like_xml(path):
        records = list(_nikto_xml_records(path))
    else:
        records = list(_nikto_json_records(path))

    out = NormaliseResult()
    for ref, host, item in records:
        msg = _clean(item.get("msg")) or _clean(item.get("description"))
        ident = _clean(item.get("id")) or _clean(item.get("OSVDB")) or _clean(item.get("osvdbid"))

        rule = table.lookup(tool, ident) or table.match(tool, msg)
        if rule is not None and rule.skip:
            continue
        cwe = rule.cwe if rule is not None else None
        if cwe is None and rule is None and table.tool_cfg(tool).get("use_reference_cwe", True):
            # ~4% of nikto's db_tests rows put a CWE id in the free-text references
            # column (CWE-552, CWE-200, CWE-548 and two hardcoded CWE-16). When nikto
            # states one itself, that beats anything we could infer from the prose.
            cwe = _first_cwe_in_text(_clean(item.get("references")))
        if cwe is None and rule is None:
            out.unmapped.append(_unmapped(tool, str(ident or "?"), msg, msg))

        uri = _clean(item.get("url")) or _clean(item.get("uri"))
        url = _join_host(host, uri)
        out.findings.append(
            Finding(
                tool=tool,
                url=url,
                method=norm_method(item.get("method")) or "GET",
                param=_param_from_url(url),
                cwe=cwe,
                name=msg,
                # Nikto grades nothing. Everything it reports is "this exists", so the
                # severity is left to the rule when the table knows one, else null.
                severity=(rule.severity if rule is not None else None),
                confidence=None,
                raw_ref=ref,
            )
        )
    return out


_ANY_CWE_RE = re.compile(r"CWE[-_ :]?(\d+)", re.IGNORECASE)


def _first_cwe_in_text(text: str | None) -> int | None:
    """CWE id stated verbatim inside a free-text field, or None."""
    if not text:
        return None
    match = _ANY_CWE_RE.search(text)
    return int(match.group(1)) if match else None


def _looks_like_xml(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(64).lstrip().startswith(b"<")
    except OSError:
        return False


def _nikto_json_records(path: Path) -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
    doc = _load_json(path)
    if doc is None:
        return
    # Nikto writes a bare object for one host and an array when -h took several.
    scans = doc if isinstance(doc, list) else [doc]
    for si, scan in enumerate(scans):
        if not isinstance(scan, dict):
            continue
        # 2.6.0 renamed `banner` to `server_banner`; both spellings are read so a
        # fixture or an older report does not silently lose the host identity.
        host = {k: scan.get(k) for k in ("host", "ip", "port", "banner", "server_banner")}
        for vi, item in enumerate(scan.get("vulnerabilities") or []):
            if isinstance(item, dict):
                yield f"{path.name}#[{si}].vulnerabilities[{vi}]", host, item


def _nikto_xml_records(path: Path) -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
    # defusedxml is not a dependency of this repo; the input is a file we produced
    # ourselves in a container we started, so stdlib ElementTree with entity
    # resolution left at its default is acceptable here and nowhere else.
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError):
        return
    for si, scan in enumerate(root.iter("scandetails")):
        host = {
            "host": scan.get("targethostname") or scan.get("targetip"),
            "ip": scan.get("targetip"),
            "port": scan.get("targetport"),
            "banner": scan.get("targetbanner"),
        }
        for vi, item in enumerate(scan.findall("item")):
            payload = {
                "id": item.get("id"),
                "osvdbid": item.get("osvdbid"),
                "method": item.get("method"),
                "uri": (item.findtext("uri") or "").strip(),
                "msg": (item.findtext("description") or "").strip(),
            }
            yield f"{path.name}#scandetails[{si}].item[{vi}]", host, payload


def _join_host(host: dict[str, Any], uri: str | None) -> str | None:
    """Rebuild an absolute URL: nikto reports the path only."""
    if uri and uri.startswith(("http://", "https://")):
        return uri
    name = _clean(host.get("host")) or _clean(host.get("ip"))
    if not name:
        return uri
    port = _clean(host.get("port"))
    scheme = "https" if port == "443" else "http"
    netloc = name if port in (None, "80", "443") else f"{name}:{port}"
    return f"{scheme}://{netloc}{uri or '/'}"


# --------------------------------------------------------------------------------
# skipfish -- the JavaScript "report" directory
# --------------------------------------------------------------------------------

# skipfish emits no JSON: its report is a set of JS assignments consumed by its own
# index.html. They are single-quoted, so they are not JSON and cannot be parsed as
# such, but they are small, deterministic and machine-written -- a tokeniser is
# enough, and far preferable to shelling out to a JS engine.
_JS_ASSIGN_RE = re.compile(r"var\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*", re.MULTILINE)


def parse_js_assignments(text: str) -> dict[str, Any]:
    """Extract ``var name = <literal>;`` pairs from a skipfish .js file."""
    out: dict[str, Any] = {}
    for match in _JS_ASSIGN_RE.finditer(text):
        name = match.group(1)
        literal, _ = _read_js_literal(text, match.end())
        if literal is None:
            continue
        try:
            out[name] = json.loads(literal)
        except json.JSONDecodeError:
            continue
    return out


def _read_js_literal(text: str, start: int) -> tuple[str | None, int]:
    """Read one JS value starting at ``start`` and return it as JSON text.

    Handles single-quoted strings (converted to JSON strings), nesting, and the
    bare ``true``/``false`` skipfish writes for ``fetched``.
    """
    i = start
    depth = 0
    buf: list[str] = []
    while i < len(text):
        ch = text[i]
        if ch in "'\"":
            value, i = _read_js_string(text, i)
            buf.append(json.dumps(value))
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth < 0:
                return None, i
        elif ch == ";" and depth == 0:
            break
        buf.append(ch)
        i += 1
        if depth == 0 and buf and buf[-1] in "]}":
            break
    literal = "".join(buf).strip().rstrip(";").strip()
    return (literal or None), i


def _read_js_string(text: str, start: int) -> tuple[str, int]:
    quote = text[start]
    i = start + 1
    chars: list[str] = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            chars.append(nxt if nxt in "'\"\\" else "\\" + nxt)
            i += 2
            continue
        if ch == quote:
            i += 1
            break
        chars.append(ch)
        i += 1
    return "".join(chars), i


def normalise_skipfish(
    path: Path, table: CweTable | None = None, tool: str = "skipfish"
) -> NormaliseResult:
    """Parse a skipfish report directory (or its ``samples.js`` directly).

    ``samples.js`` is used rather than ``issue_index.js`` because it is the only file
    that carries the *URL* of each sample, and a finding without a URL cannot be
    matched against a catalog entrypoint. The trade-off is real and is recorded here
    rather than buried: skipfish caps that file at MAX_SAMPLES (1024) samples per
    issue type, so a run that overflows the cap under-reports duplicate instances of
    the same issue. It never loses an issue *type*.

    skipfish reports no HTTP method, so ``method`` stays null: emitting "GET" would
    be a guess, and the scorer treats null as "unspecified" rather than as a mismatch.
    """
    table = table or default_table()
    out = NormaliseResult()
    samples_path = path / "samples.js" if path.is_dir() else path
    if not samples_path.exists():
        return out

    doc = parse_js_assignments(samples_path.read_text(encoding="utf-8", errors="replace"))
    issue_samples = doc.get("issue_samples")
    if not isinstance(issue_samples, list):
        return out

    severity_map = {
        str(k): v for k, v in (table.tool_cfg(tool).get("severity_map") or {}).items()
    }
    for gi, group in enumerate(issue_samples):
        if not isinstance(group, dict):
            continue
        type_code = _clean(group.get("type"))
        rule = table.lookup(tool, type_code)
        if rule is not None and rule.skip:
            continue
        cwe = rule.cwe if rule is not None else None
        name = (rule.name if rule is not None else None) or (
            f"skipfish issue type {type_code}" if type_code else None
        )
        if cwe is None and rule is None:
            out.unmapped.append(_unmapped(tool, str(type_code or "?"), name))
        severity = norm_severity(severity_map.get(str(group.get("severity"))))

        for si, sample in enumerate(group.get("samples") or []):
            if not isinstance(sample, dict):
                continue
            url = _clean(sample.get("url"))
            out.findings.append(
                Finding(
                    tool=tool,
                    url=url,
                    method=None,
                    # 'extra' is skipfish's slot for the offending parameter name on
                    # injection issues, and free text on everything else. Only used
                    # when it actually names a query parameter of the sample URL.
                    param=_skipfish_param(sample.get("extra"), url),
                    cwe=cwe,
                    name=name,
                    severity=severity,
                    confidence=None,
                    raw_ref=f"{samples_path.name}#issue_samples[{gi}].samples[{si}]",
                )
            )
    return out


def _skipfish_param(extra: Any, url: str | None) -> str | None:
    candidate = _clean(extra)
    if candidate is None:
        return _param_from_url(url)
    if url and f"{candidate}=" in url:
        return candidate
    # `extra` also carries things like a MIME type or a header name; only accept it
    # as a parameter when the URL confirms it is one.
    return _param_from_url(url)


# --------------------------------------------------------------------------------
# generic -- user-supplied JSON or SARIF, for tools we have no driver for
# --------------------------------------------------------------------------------

# Key aliases accepted in a hand-supplied JSON file. Vendors each pick a different
# spelling and asking them to rename fields is a worse deal than accepting synonyms.
_GENERIC_ALIASES: dict[str, tuple[str, ...]] = {
    "url": ("url", "uri", "location", "target", "endpoint", "affected_url", "request_url"),
    "method": ("method", "http_method", "verb", "request_method"),
    "param": ("param", "parameter", "input", "field", "injection_point", "affected_input"),
    "cwe": ("cwe", "cwe_id", "cweid", "cwe-id", "cwes"),
    "name": ("name", "title", "issue", "vulnerability", "rule", "check", "template"),
    "severity": ("severity", "risk", "criticality", "impact", "level"),
    "confidence": ("confidence", "certainty"),
    "id": ("id", "finding_id", "issue_id", "ref", "uuid"),
}


def normalise_generic(
    path: Path, table: CweTable | None = None, tool: str = "generic"
) -> NormaliseResult:
    """Ingest a findings file supplied by a vendor: SARIF 2.1.0 or free-form JSON.

    This is how a commercial PTaaS product gets benchmarked without us reverse
    engineering its API: it exports what it claims to have found, we map it with the
    same rules as everyone else and score it with the same engine.

    The vendor's own CWE is trusted when present. When it is absent we do **not**
    infer one from the title, for the reason in the module docstring, and the run
    report shows how many of the vendor's findings arrived without a CWE.
    """
    table = table or default_table()
    doc = _load_json(path)
    if doc is None:
        return NormaliseResult()
    if isinstance(doc, dict) and "runs" in doc and ("$schema" in doc or "version" in doc):
        return _normalise_sarif(doc, path, table, tool)
    return _normalise_generic_json(doc, path, table, tool)


def _pick(rec: dict[str, Any], field: str) -> Any:
    for key in _GENERIC_ALIASES[field]:
        if key in rec and rec[key] not in (None, ""):
            return rec[key]
        # tolerate CamelCase and dashes without enumerating every spelling
        for actual in rec:
            if isinstance(actual, str) and actual.lower().replace("-", "_") == key:
                if rec[actual] not in (None, ""):
                    return rec[actual]
    return None


def _normalise_generic_json(
    doc: Any, path: Path, table: CweTable, tool: str
) -> NormaliseResult:
    out = NormaliseResult()
    if isinstance(doc, dict):
        for key in ("findings", "results", "issues", "vulnerabilities", "items"):
            if isinstance(doc.get(key), list):
                doc = doc[key]
                break
        else:
            doc = [doc]
    if not isinstance(doc, list):
        return out

    for ri, rec in enumerate(doc):
        if not isinstance(rec, dict):
            continue
        ident = _clean(_pick(rec, "id"))
        name = _clean(_pick(rec, "name"))
        rule = table.lookup(tool, ident, name)
        if rule is not None and rule.skip:
            continue
        cwe = rule.cwe if rule is not None else parse_cwe(_pick(rec, "cwe"))
        if cwe is None and rule is None:
            out.unmapped.append(_unmapped(tool, str(ident or name or "?"), name))
        url = _clean(_pick(rec, "url"))
        out.findings.append(
            Finding(
                tool=tool,
                url=url,
                method=norm_method(_pick(rec, "method")),
                param=_clean(_pick(rec, "param")) or _param_from_url(url),
                cwe=cwe,
                name=name,
                severity=norm_severity(_pick(rec, "severity")),
                confidence=norm_confidence(_pick(rec, "confidence")),
                raw_ref=f"{path.name}#[{ri}]",
            )
        )
    return out


# SARIF carries CWEs in three places depending on who wrote the file.
_SARIF_TAG_RE = re.compile(r"CWE[-_ :]?(\d+)", re.IGNORECASE)


def _normalise_sarif(doc: dict[str, Any], path: Path, table: CweTable, tool: str) -> NormaliseResult:
    out = NormaliseResult()
    for ri, run in enumerate(doc.get("runs") or []):
        if not isinstance(run, dict):
            continue
        rules_by_id = _sarif_rules(run)
        for si, result in enumerate(run.get("results") or []):
            if not isinstance(result, dict):
                continue
            rule_id = _clean(result.get("ruleId"))
            rule_meta = rules_by_id.get(rule_id or "", {})
            name = (
                _clean(_dig(result, "message.text"))
                or _clean(_dig(rule_meta, "shortDescription.text"))
                or _clean(rule_meta.get("name"))
                or rule_id
            )
            table_rule = table.lookup(tool, rule_id)
            cwe = table_rule.cwe if table_rule is not None else _sarif_cwe(result, rule_meta)
            if cwe is None and table_rule is None:
                out.unmapped.append(_unmapped(tool, str(rule_id or "?"), name))

            url, method, param = _sarif_location(result)
            out.findings.append(
                Finding(
                    tool=tool,
                    url=url,
                    method=method,
                    param=param or _param_from_url(url),
                    cwe=cwe,
                    name=name,
                    severity=_sarif_severity(result, rule_meta),
                    confidence=norm_confidence(_dig(result, "properties.confidence")),
                    raw_ref=f"{path.name}#runs[{ri}].results[{si}]",
                )
            )
    return out


def _sarif_rules(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rules: dict[str, dict[str, Any]] = {}
    for driver_key in ("driver",):
        driver = _dig(run, f"tool.{driver_key}") or {}
        for rule in driver.get("rules") or []:
            if isinstance(rule, dict) and rule.get("id"):
                rules[str(rule["id"])] = rule
    for ext in _dig(run, "tool.extensions") or []:
        for rule in (ext or {}).get("rules") or []:
            if isinstance(rule, dict) and rule.get("id"):
                rules.setdefault(str(rule["id"]), rule)
    return rules


def _sarif_cwe(result: dict[str, Any], rule_meta: dict[str, Any]) -> int | None:
    for candidate in (
        _dig(result, "properties.cwe"),
        _dig(rule_meta, "properties.cwe"),
    ):
        got = parse_cwe(candidate)
        if got is not None:
            return got
    tags: list[Any] = []
    for source in (_dig(rule_meta, "properties.tags"), _dig(result, "properties.tags")):
        if isinstance(source, list):
            tags.extend(source)
    for taxa in (result.get("taxa") or []) + (rule_meta.get("relationships") or []):
        target = taxa.get("target") if isinstance(taxa, dict) else None
        if isinstance(target, dict):
            tags.extend([target.get("id"), target.get("name")])
        elif isinstance(taxa, dict):
            tags.append(taxa.get("id"))
    for tag in tags:
        if tag is None:
            continue
        match = _SARIF_TAG_RE.search(str(tag))
        if match:
            return int(match.group(1))
    return None


def _sarif_severity(result: dict[str, Any], rule_meta: dict[str, Any]) -> str | None:
    # security-severity is a CVSS-like float; SARIF's own `level` only has
    # error/warning/note, which is not a security scale at all.
    raw = _dig(result, "properties.security-severity") or _dig(
        rule_meta, "properties.security-severity"
    )
    try:
        score = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        score = None
    if score is not None:
        if score >= 9.0:
            return "critical"
        if score >= 7.0:
            return "high"
        if score >= 4.0:
            return "medium"
        if score > 0:
            return "low"
        return "info"
    return norm_severity(result.get("level")) or norm_severity(
        _dig(rule_meta, "defaultConfiguration.level")
    )


def _sarif_location(result: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Pull (url, method, param) out of a SARIF result.

    DAST tools use the ``webRequest`` object; everything else falls back to the
    physical location URI, which for a web finding is the URL.
    """
    request = result.get("webRequest") if isinstance(result.get("webRequest"), dict) else {}
    url = _clean(request.get("target"))
    method = norm_method(request.get("method"))
    param = None
    params = request.get("parameters")
    if isinstance(params, dict) and len(params) == 1:
        param = _clean(next(iter(params)))
    if url is None:
        for loc in result.get("locations") or []:
            uri = _dig(loc, "physicalLocation.artifactLocation.uri")
            if uri:
                url = _clean(uri)
                break
    return url, method, param


# --------------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------------

PARSERS: dict[str, Callable[..., NormaliseResult]] = {
    "zap": normalise_zap,
    "nuclei": normalise_nuclei,
    "wapiti": normalise_wapiti,
    "nikto": normalise_nikto,
    "skipfish": normalise_skipfish,
    "generic": normalise_generic,
}


def normalise(tool: str, path: Path, table: CweTable | None = None) -> NormaliseResult:
    """Normalise one raw report from ``tool``."""
    try:
        parser = PARSERS[tool]
    except KeyError:
        raise KeyError(f"no parser for tool {tool!r}; known: {sorted(PARSERS)}") from None
    return parser(path, table=table, tool=tool)
