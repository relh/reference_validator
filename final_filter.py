#!/usr/bin/env python3

import re
import requests
import time
import logging
import xml.etree.ElementTree as ET
from urllib.parse import quote
import difflib
import json

import bs4  # from bs4 import BeautifulSoup
# pip install scholarly
from scholarly import scholarly, ProxyGenerator  # For advanced usage with proxies

###############################################################################
# Global Configuration
###############################################################################

SLEEP_SECONDS = 1.0
TITLE_MATCH_THRESHOLD = 75  # approximate matching threshold
SCHOLAR_TITLE_THRESHOLD = 75

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

###############################################################################
# 1) Approximate Matching Helpers
###############################################################################

def approximate_ratio(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100

###############################################################################
# 2) Minimal Searching Example (CVF, NeurIPS, OpenReview) 
###############################################################################

def search_cvf(title):
    """(Same as before)"""
    base_url = "https://openaccess.thecvf.com/search"
    params = {"q": title}
    try:
        resp = requests.get(base_url, params=params, timeout=10)
        if resp.status_code != 200:
            logging.debug("CVF search HTTP error: %s", resp.status_code)
            return False

        soup = bs4.BeautifulSoup(resp.text, "html.parser")
        results = soup.select("div.bibref h5.ptitle a")
        if not results:
            logging.debug("No CVF search results for '%s'", title)
            return False

        for r in results:
            found_title = r.get_text(strip=True)
            ratio = approximate_ratio(title, found_title)
            logging.debug("  CVF candidate: '%s' ratio=%.1f", found_title, ratio)
            if ratio >= TITLE_MATCH_THRESHOLD:
                logging.debug("CVF => Found match with ratio=%.1f for '%s'", ratio, title)
                return True
        return False
    except Exception as e:
        logging.debug("Exception in CVF search: %s", e)
        return False
    finally:
        time.sleep(SLEEP_SECONDS)

def search_neurips(title):
    """(Same as before)"""
    base_url = "https://papers.nips.cc/paper_search"
    params = {"query": title}
    try:
        resp = requests.get(base_url, params=params, timeout=10)
        if resp.status_code != 200:
            logging.debug("NeurIPS search HTTP error: %s", resp.status_code)
            return False

        soup = bs4.BeautifulSoup(resp.text, "html.parser")
        results = soup.select("div.posters li a")
        if not results:
            logging.debug("No NeurIPS search results found for '%s'", title)
            return False

        for r in results:
            found_title = r.get_text(strip=True)
            ratio = approximate_ratio(title, found_title)
            logging.debug("  NeurIPS candidate: '%s' ratio=%.1f", found_title, ratio)
            if ratio >= TITLE_MATCH_THRESHOLD:
                logging.debug("NeurIPS => Found match with ratio=%.1f for '%s'", ratio, title)
                return True
        return False
    except Exception as e:
        logging.debug("Exception in NeurIPS search: %s", e)
        return False
    finally:
        time.sleep(SLEEP_SECONDS)

def search_openreview(title):
    """(Same as before)"""
    base_url = "https://api.openreview.net/notes"
    params = {"content.title": title, "details": "all", "limit": 10}
    try:
        resp = requests.get(base_url, params=params, timeout=10)
        if resp.status_code != 200:
            logging.debug("OpenReview search HTTP error: %s", resp.status_code)
            return False

        data = resp.json()
        notes = data.get("notes", [])
        if not notes:
            logging.debug("No OpenReview search results found for '%s'", title)
            return False

        for idx, n in enumerate(notes):
            c_title = n.get("content", {}).get("title", "")
            ratio = approximate_ratio(title, c_title)
            logging.debug("  OpenReview candidate#%d: '%s' ratio=%.1f", idx, c_title, ratio)
            if ratio >= TITLE_MATCH_THRESHOLD:
                logging.debug("OpenReview => Found match with ratio=%.1f for '%s'", ratio, title)
                return True
        return False
    except Exception as e:
        logging.debug("Exception in OpenReview search: %s", e)
        return False
    finally:
        time.sleep(SLEEP_SECONDS)

###############################################################################
# 3) Google Scholar fallback
###############################################################################

def search_google_scholar(title):
    """
    Use 'scholarly' to query Google Scholar for 'title'.
    If we find any result with approximate ratio >= SCHOLAR_TITLE_THRESHOLD,
    we return True, else False.

    This handles the case where 'scholarly' returns dict objects instead of
    an object with .bib attributes.
    """
    SCHOLAR_TITLE_THRESHOLD = 75
    max_results = 5  # how many search results to examine

    try:
        search_results = scholarly.search_pubs(title)  # This is a generator
        count = 0
        for result in search_results:
            count += 1
            if isinstance(result, dict):
                # Newer scholarly versions often yield dict objects with nested info
                if 'bib' in result and isinstance(result['bib'], dict):
                    pub_title = result['bib'].get('title', '')
                else:
                    # Fallback if the dict is in some other structure
                    pub_title = result.get('title', '')
            else:
                # If we’re dealing with an older version that returns a custom object
                pub_title = getattr(result.bib, 'title', '') or ''

            ratio = approximate_ratio(title, pub_title)
            logging.debug("  Scholar candidate: '%s' ratio=%.1f", pub_title, ratio)
            if ratio >= SCHOLAR_TITLE_THRESHOLD:
                logging.debug("Google Scholar => Found match with ratio=%.1f for '%s'", ratio, title)
                return True

            if count >= max_results:
                break

        return False

    except Exception as e:
        logging.debug("Exception in Google Scholar search: %s", e)
        return False

    finally:
        time.sleep(SLEEP_SECONDS)

###############################################################################
# 4) Reading flagged_references.txt
###############################################################################

def load_flagged_references(flagged_file_path):
    """
    Minimal approach: 
    Lines presumably look like:
      [someID] No match in Crossref or ArXiv for 'Paper Title'
    We'll parse out (someID, Paper Title).
    If no quoted title is found, store None for the title.
    """
    import re
    flagged = []
    pattern = re.compile(r"^\[(?P<id>[^\]]+)\].+['\"](?P<title>[^'\"]+)['\"].*")
    with open(flagged_file_path, "r", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if m:
                ref_id = m.group("id").strip()
                ref_title = m.group("title").strip()
                flagged.append((ref_id, ref_title))
            else:
                # fallback parse for ID
                bracket_m = re.search(r"\[(?P<id>[^\]]+)\]", line)
                if bracket_m:
                    ref_id = bracket_m.group("id").strip()
                else:
                    ref_id = "UNKNOWN"
                flagged.append((ref_id, None))
    return flagged

###############################################################################
# 5) Main script
###############################################################################

def main(flagged_file_path, bad_file_path="bad_references.txt"):
    """
    1) Load lines from 'flagged_references.txt'
    2) For each reference:
       - Try CVF
       - If not found => NeurIPS
       - If not found => OpenReview
       - If still not found => Google Scholar
       - If still not found => "bad reference"
    3) Write the 'bad references' to 'bad_file_path'
    """
    flagged_entries = load_flagged_references(flagged_file_path)
    bad_references = []

    for ref_id, ref_title in flagged_entries:
        if not ref_title:
            msg = f"{ref_id}: missing or unparseable title"
            logging.debug(msg)
            bad_references.append(msg)
            continue

        logging.debug("-----")
        logging.debug("Processing flagged ref: ID=%s, Title='%s'", ref_id, ref_title)

        # 1) CVF
        if search_cvf(ref_title):
            continue
        # 2) NeurIPS
        if search_neurips(ref_title):
            continue
        # 3) OpenReview
        if search_openreview(ref_title):
            continue
        # 4) Google Scholar
        if search_google_scholar(ref_title):
            continue

        # If all fail
        msg = f"{ref_id}: Not found on CVF / NeurIPS / OpenReview / Google Scholar => '{ref_title}'"
        logging.debug(msg)
        bad_references.append(msg)

    # Write out the final "bad references"
    with open(bad_file_path, "w", encoding="utf-8") as fout:
        for item in bad_references:
            fout.write(item + "\n")

    if not bad_references:
        print("No bad references; all flagged items were found somewhere!")
    else:
        print("Some references remain not found; see:", bad_file_path)
        for br in bad_references:
            print(" -", br)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python fallback_scholar.py path/to/flagged_references.txt [bad_refs.txt]")
        sys.exit(1)

    flagged_path = sys.argv[1]
    if len(sys.argv) > 2:
        bad_path = sys.argv[2]
    else:
        bad_path = "bad_references.txt"

    main(flagged_path, bad_path)


