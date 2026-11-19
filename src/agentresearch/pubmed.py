"""Fetch a bounded, query-specific collection of PubMed abstract records."""

import json
import re
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from xml.etree.ElementTree import TreeBuilder
from xml.parsers import expat

from .schema import Corpus, Sentence, Source, integer, string

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
MAX_BYTES = 5 * 1024 * 1024
_next_request = 0
_rate_lock = threading.Lock()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise ValueError("PubMed redirect rejected")


def _open(request, timeout):
    # Use the OS trust store, supplemented on Python installations with no system roots.
    context = ssl.create_default_context()
    if not context.get_ca_certs():
        try:
            import certifi
        except ImportError:
            pass
        else:
            context.load_verify_locations(certifi.where())
    return build_opener(_NoRedirect(), HTTPSHandler(context=context)).open(
        request, timeout=timeout
    )


def _request(endpoint, params):
    global _next_request
    url = BASE + endpoint + "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": "AgentResearch/0.1"})
    for attempt in range(3):
        with _rate_lock:
            wait = _next_request - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _next_request = time.monotonic() + 0.4
        try:
            with _open(request, timeout=15) as response:
                result = response.read(MAX_BYTES + 1)
            if len(result) > MAX_BYTES:
                raise ValueError("PubMed response exceeds size limit")
            return result
        except HTTPError as exc:
            exc.close()
            if attempt == 2 or exc.code not in (408, 429, 500, 502, 503, 504):
                raise ValueError(f"PubMed returned HTTP {exc.code}") from None
        except (URLError, TimeoutError, ConnectionError) as exc:
            if attempt == 2:
                raise ValueError(
                    "PubMed connection failed after three attempts"
                ) from exc
        time.sleep(2**attempt)


def _xml_tree(raw):
    tree = TreeBuilder()
    parser = expat.ParserCreate()
    parser.StartElementHandler = tree.start
    parser.EndElementHandler = tree.end
    parser.CharacterDataHandler = tree.data

    def reject(*_):
        raise ValueError("unsupported PubMed XML entity or declaration")

    def doctype(name, system_id, public_id, has_internal_subset):
        if has_internal_subset or name != "PubmedArticleSet":
            reject()

    parser.StartDoctypeDeclHandler = doctype
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    try:
        parser.Parse(raw, True)
    except expat.ExpatError as exc:
        raise ValueError("invalid PubMed XML") from exc
    return tree.close()


def _abstract(record, pmid):
    article = record.find("./MedlineCitation/Article")
    if article is None:
        return None
    title = article.find("ArticleTitle")
    sections = [
        "".join(el.itertext()) for el in article.findall("Abstract/AbstractText")
    ]
    text = "\n".join(sections)
    if not text.strip():
        return None
    sentences = []
    # Boundaries are a transparent punctuation/paragraph heuristic.
    start = 0
    for boundary in re.finditer(r"(?<=[.!?])\s+|\n+|\Z", text):
        part = text[start : boundary.start()]
        if part.strip():
            sentences.append(
                Sentence(
                    str(len(sentences)),
                    start + len(part) - len(part.lstrip()),
                    start + len(part.rstrip()),
                )
            )
        start = boundary.end()
    return Source(
        "pubmed:" + pmid,
        "".join(title.itertext()) if title is not None else "",
        text,
        tuple(sentences),
        f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    )


def fetch_corpus(query, max_records=10, email=None):
    integer(max_records, 1, 50)
    if len(string(query)) > 1000:
        raise ValueError("PubMed query exceeds 1000 characters")
    params = {"db": "pubmed", "tool": "agentresearch"}
    if email is not None:
        if len(string(email)) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+", email):
            raise ValueError("invalid contact email")
        params["email"] = email
    raw = _request(
        "esearch.fcgi",
        {**params, "term": query, "retmax": max_records, "retmode": "json"},
    )
    try:
        ids = json.loads(raw)["esearchresult"]["idlist"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid PubMed search response") from exc
    if (
        not isinstance(ids, list)
        or len(ids) > max_records
        or not all(isinstance(i, str) and re.fullmatch(r"[0-9]{1,12}", i) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("invalid PubMed search IDs")
    if not ids:
        return Corpus(())
    root = _xml_tree(
        _request("efetch.fcgi", {**params, "id": ",".join(ids), "retmode": "xml"})
    )
    if root.tag != "PubmedArticleSet" or root.find(".//ERROR") is not None:
        raise ValueError("unexpected PubMed fetch response")
    found = {}
    for record in root:
        if record.tag == "PubmedArticle":
            pmid = record.findtext("./MedlineCitation/PMID")
        elif record.tag == "PubmedBookArticle":
            pmid = record.findtext("./BookDocument/PMID")
        else:
            raise ValueError("unexpected PubMed record")
        if pmid not in ids or pmid in found:
            raise ValueError("unrequested or duplicate PubMed record")
        found[pmid] = _abstract(record, pmid)
    if set(found) != set(ids):
        raise ValueError("incomplete PubMed response")
    return Corpus(tuple(found[i] for i in ids if found[i] is not None))
