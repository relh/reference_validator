#!/usr/bin/env python3

import requests
import bibtexparser
import re
import time
import difflib
import logging
import xml.etree.ElementTree as ET

###############################################################################
# Global Configuration
###############################################################################

CROSSREF_ROWS = 5       # Max Crossref search results to examine
ARXIV_ROWS    = 5       # Max ArXiv search results to examine
SLEEP_SECONDS = 0.5     # Delay after each web request to avoid rate-limits

TITLE_MATCH_THRESHOLD = 75  # min approximate match ratio (0..100) for titles
AUTHOR_MATCH_THRESHOLD = 75 # fuzzy ratio threshold for last-name matches
MIN_AUTHOR_OVERLAP_FRAC = 0.6  # fraction of .bib authors we must match

###############################################################################
# Logging Configuration
###############################################################################
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)

###############################################################################
# Name Parsing & Fuzzy Matching
###############################################################################

def parse_last_name(author_str):
    """
    Extract a canonical 'last name' from an author string.
    Handles two common patterns:
      1) "LastName, FirstName ..."  (BibTeX style)
      2) "FirstName MiddleName ... LastName"  (ArXiv style)
    Returns a lowercase last name (hyphens kept).
    """
    author_str = author_str.strip()
    if not author_str:
        return ""

    # Convert to lower
    a_str = author_str.lower()

    # Check if there's a comma => "lastname, firstname"
    if ',' in a_str:
        parts = a_str.split(',', maxsplit=1)
        last = parts[0].strip()
        return last
    else:
        # Otherwise, assume "firstname ... lastname"
        parts = a_str.split()
        last = parts[-1]
        return last

def fuzzy_ratio(a, b):
    """
    Return difflib ratio * 100, in [0..100].
    """
    return difflib.SequenceMatcher(None, a, b).ratio() * 100

def authors_overlap_fuzzy(bib_authors, found_authors):
    """
    For each "parsed last name" in bib_authors, see if there's a
    matching last name in found_authors above AUTHOR_MATCH_THRESHOLD.
    Then measure fraction of bib_authors that found a match.
    """
    # Convert each list of raw authors to last-name strings
    bib_last = [parse_last_name(x) for x in bib_authors if x.strip()]
    found_last = [parse_last_name(x) for x in found_authors if x.strip()]

    if not bib_last:
        # If the .bib has no authors, treat as matched
        return True

    matched_count = 0
    used_found_idx = set()

    for bln in bib_last:
        # See if there's a found_last that is fuzzy-close enough
        best_score = 0
        best_idx = -1
        for i, fln in enumerate(found_last):
            if i in used_found_idx:
                continue
            score = fuzzy_ratio(bln, fln)
            if score > best_score:
                best_score = score
                best_idx = i
        
        if best_score >= AUTHOR_MATCH_THRESHOLD:
            matched_count += 1
            used_found_idx.add(best_idx)

    overlap_fraction = matched_count / len(bib_last)
    logging.debug(
        "Fuzzy author overlap: matched=%d / totalBib=%d => frac=%.2f",
        matched_count, len(bib_last), overlap_fraction
    )
    return overlap_fraction >= MIN_AUTHOR_OVERLAP_FRAC

###############################################################################
# Approximate Title Match
###############################################################################

def approximate_title_match(bib_title, candidate_title):
    """
    Return True if the approximate match ratio is >= TITLE_MATCH_THRESHOLD.
    """
    ratio = fuzzy_ratio(bib_title.lower(), candidate_title.lower())
    logging.debug(
        "   Checking title fuzzy ratio => '%.60s...' vs '%.60s...' => %.1f",
        bib_title, candidate_title, ratio
    )
    return (ratio >= TITLE_MATCH_THRESHOLD)

###############################################################################
# Crossref Search
###############################################################################

def find_on_crossref(bib_title, bib_authors):
    """
    Search Crossref by (approximate) title. If we find a candidate whose
    title & authors match, return True. Otherwise False.
    """
    url = (
        "https://api.crossref.org/works?"
        f"query.bibliographic={requests.utils.quote(bib_title)}"
        f"&rows={CROSSREF_ROWS}"
    )
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            logging.debug("Crossref query failed with HTTP %s", resp.status_code)
            return False

        items = resp.json().get('message', {}).get('items', [])
        if not items:
            logging.debug("No Crossref results for title='%s'", bib_title)
            return False

        for idx, it in enumerate(items):
            c_titles = it.get('title', [])  # list
            c_title  = c_titles[0] if c_titles else ""
            c_authors = []
            for a in it.get('author', []):
                given = a.get('given', "")
                fam   = a.get('family', "")
                full  = (given + " " + fam).strip()
                if full:
                    c_authors.append(full)

            logging.debug("Crossref candidate #%d: title='%s', authors=%s", idx, c_title, c_authors)
            if approximate_title_match(bib_title, c_title):
                # Title matched => check authors
                if authors_overlap_fuzzy(bib_authors, c_authors):
                    logging.debug("Crossref matched entry => %s", bib_title)
                    return True
        return False

    except Exception as e:
        logging.debug("Exception in Crossref search: %s", e)
        return False
    finally:
        time.sleep(SLEEP_SECONDS)

###############################################################################
# ArXiv Search (fallback)
###############################################################################

def find_on_arxiv(bib_title, bib_authors):
    """
    Search ArXiv by title. If we find a candidate whose
    title & authors match, return True. Otherwise False.
    """
    # We do a naive phrase search: ti:"<title>"
    base_url = "http://export.arxiv.org/api/query?"
    params   = (
        f"search_query=ti:%22{requests.utils.quote(bib_title)}%22"
        f"&start=0&max_results={ARXIV_ROWS}"
    )
    full_url = base_url + params
    try:
        resp = requests.get(full_url, timeout=10)
        if resp.status_code != 200:
            logging.debug("ArXiv query failed with HTTP %s", resp.status_code)
            return False

        # ArXiv returns Atom XML
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        if not entries:
            logging.debug("No ArXiv results for title='%s'", bib_title)
            return False

        for idx, entry in enumerate(entries):
            title_elt = entry.find("atom:title", ns)
            if title_elt is not None:
                c_title = title_elt.text.strip()
            else:
                c_title = ""
            author_nodes = entry.findall("atom:author", ns)
            c_authors = []
            for nd in author_nodes:
                name_elt = nd.find("atom:name", ns)
                if name_elt is not None:
                    c_authors.append(name_elt.text.strip())

            logging.debug("ArXiv candidate #%d: title='%s', authors=%s", idx, c_title, c_authors)
            if approximate_title_match(bib_title, c_title):
                if authors_overlap_fuzzy(bib_authors, c_authors):
                    logging.debug("ArXiv matched entry => %s", bib_title)
                    return True
        return False
    except Exception as e:
        logging.debug("Exception in ArXiv search: %s", e)
        return False
    finally:
        time.sleep(SLEEP_SECONDS)

###############################################################################
# Main
###############################################################################

def check_bibliography(bib_file_path):
    """
    Parse each .bib entry, attempt a Crossref match.
    If Crossref fails, fallback to ArXiv. 
    Flag the entry if both fail.
    """
    with open(bib_file_path, 'r', encoding='utf-8') as f:
        bib_db = bibtexparser.load(f)

    flagged = []

    for entry in bib_db.entries:
        entry_id = entry.get('ID', 'UNKNOWN_ID')
        bib_title = entry.get('title', "").strip()
        bib_authors_str = entry.get('author', "")
        bib_authors = [x.strip() for x in bib_authors_str.split(" and ") if x.strip()]

        logging.debug("-----")
        logging.debug("Processing entry: %s", entry_id)
        logging.debug("Bib Title: '%s'", bib_title)
        logging.debug("Bib Authors: %s", bib_authors)

        if not bib_title:
            reason = f"[{entry_id}] Missing title in .bib"
            flagged.append((entry_id, reason))
            logging.debug("Flagged: %s", reason)
            continue

        # 1) Try Crossref
        found_xref = find_on_crossref(bib_title, bib_authors)

        # 2) If not found, fallback to ArXiv
        found_arxiv = False
        if not found_xref:
            found_arxiv = find_on_arxiv(bib_title, bib_authors)

        if not found_xref and not found_arxiv:
            reason = f"[{entry_id}] No match in Crossref or ArXiv for '{bib_title}'"
            flagged.append((entry_id, reason))
            logging.debug("Flagged: %s", reason)

    # Write flagged references to disk
    out_file = "flagged_references.txt"
    with open(out_file, 'w', encoding='utf-8') as fout:
        for (key, reason) in flagged:
            fout.write(f"{reason}\n")

    return flagged

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python check_bibliography.py path/to/refs.bib")
        sys.exit(1)

    bib_path = sys.argv[1]
    flagged_refs = check_bibliography(bib_path)

    if not flagged_refs:
        print("All references matched in Crossref or ArXiv!")
    else:
        print("Some references were flagged. See 'flagged_references.txt' for details:")
        for (k, msg) in flagged_refs:
            print(f" - {msg}")

