#!/usr/bin/env python3

import re
import requests
import time
import logging
from urllib.parse import quote
from bs4 import BeautifulSoup  # pip install beautifulsoup4
import difflib
import json

###############################################################################
# Configuration
###############################################################################

SLEEP_SECONDS = 1.0  # time to sleep between queries to avoid hammering sites
TITLE_MATCH_THRESHOLD = 75  # minimum difflib ratio in [0..100] to consider "found"

###############################################################################
# Logging
###############################################################################
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

###############################################################################
# Helpers
###############################################################################

def approximate_ratio(a, b):
    """
    Compute rough similarity ratio in [0..100]
    using difflib.SequenceMatcher.
    """
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100

def load_flagged_references(flagged_file_path):
    """
    Reads lines of the form:
      [some_id] Some reason text including 'Title of the paper'
    Extracts:
      - reference_id (some_id)
      - title (the part in single or double quotes)

    Returns a list of tuples: [(ref_id, title_string), ...]
    If no title is found, returns None as the second field.
    """
    flagged_entries = []
    pattern = re.compile(r"^\[(?P<id>[^\]]+)\].+['\"](?P<title>[^'\"]+)['\"].*")

    with open(flagged_file_path, "r", encoding="utf-8") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue

            match = pattern.match(line)
            if match:
                ref_id = match.group("id").strip()
                title_text = match.group("title").strip()
                flagged_entries.append((ref_id, title_text))
            else:
                # If we can't parse out the ID or the quoted title, store None
                # This means line is not in the expected format
                logging.debug("Could not parse line for title: '%s'", line)
                # We still keep the line’s ID if possible
                # Attempt a simpler parse for ID
                bracket_pattern = re.compile(r"\[(?P<id>[^\]]+)\]")
                bracket_match = bracket_pattern.search(line)
                if bracket_match:
                    ref_id = bracket_match.group("id").strip()
                else:
                    ref_id = "UNKNOWN"
                flagged_entries.append((ref_id, None))
    return flagged_entries

###############################################################################
# Searching CVF
###############################################################################

def search_cvf(title):
    """
    Searches openaccess.thecvf.com by title.
    Returns True if we find a close enough match, else False.
    """
    base_url = "https://openaccess.thecvf.com/search"
    params = {"q": title}
    try:
        resp = requests.get(base_url, params=params, timeout=10)
        if resp.status_code != 200:
            logging.debug("CVF search HTTP error: %s", resp.status_code)
            return False

        soup = BeautifulSoup(resp.text, "html.parser")
        # Typical results appear under .bibref > .title, or .ptitle, etc.
        # We'll look for links under h4 or h5 with 'ptitle' class, for instance:
        results = soup.select("div.bibref h5.ptitle a")
        # If not found, the site might have changed structure. We can adapt as needed.
        if not results:
            logging.debug("No CVF search results found for '%s'", title)
            return False

        # Check approximate ratio for each found title
        for r in results:
            found_title = r.get_text(strip=True)
            ratio = approximate_ratio(title, found_title)
            logging.debug(
                "  CVF candidate: '%s' ratio=%.1f", found_title, ratio
            )
            if ratio >= TITLE_MATCH_THRESHOLD:
                logging.debug("CVF => Found match with ratio=%.1f for '%s'", ratio, title)
                return True
        return False

    except Exception as e:
        logging.debug("Exception in CVF search: %s", e)
        return False
    finally:
        time.sleep(SLEEP_SECONDS)

###############################################################################
# Searching NeurIPS
###############################################################################

def search_neurips(title):
    """
    Searches https://papers.nips.cc/ by title.
    There's a 'paper_search' GET endpoint: 
       https://papers.nips.cc/paper_search?query=<title>
    We do naive HTML parse.
    Returns True if match found, else False.
    """
    base_url = "https://papers.nips.cc/paper_search"
    params = {"query": title}
    try:
        resp = requests.get(base_url, params=params, timeout=10)
        if resp.status_code != 200:
            logging.debug("NeurIPS search HTTP error: %s", resp.status_code)
            return False

        soup = BeautifulSoup(resp.text, "html.parser")
        # The results often appear in <div class="posters"> with <li> containing <a>...
        results = soup.select("div.posters li a")
        if not results:
            logging.debug("No NeurIPS search results found for '%s'", title)
            return False

        for r in results:
            # link text is typically "Title of the Paper"
            found_title = r.get_text(strip=True)
            ratio = approximate_ratio(title, found_title)
            logging.debug(
                "  NeurIPS candidate: '%s' ratio=%.1f", found_title, ratio
            )
            if ratio >= TITLE_MATCH_THRESHOLD:
                logging.debug("NeurIPS => Found match with ratio=%.1f for '%s'", ratio, title)
                return True
        return False

    except Exception as e:
        logging.debug("Exception in NeurIPS search: %s", e)
        return False
    finally:
        time.sleep(SLEEP_SECONDS)

###############################################################################
# Searching OpenReview
###############################################################################

def search_openreview(title):
    """
    Queries OpenReview's public API for a note with a matching title.
    Because there's no direct "search by title" param, we can approximate:
       GET /notes?content.title=<title>&details=all
    We'll examine the 'notes' and do approximate matching. 
    """
    # For advanced usage, see https://github.com/openreview/openreview-py
    # or https://api.openreview.net/
    base_url = "https://api.openreview.net/notes"
    # We'll do an approximate approach: content.title is a partial match
    # We might also need other search fields. For demonstration only.
    params = {
        "content.title": title,
        "details": "all",
        "limit": 10
    }

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
            logging.debug(
                "  OpenReview candidate#%d: '%s' ratio=%.1f",
                idx, c_title, ratio
            )
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
# Main
###############################################################################

def main(flagged_file_path, bad_file_path="bad_references.txt"):
    """
    1) Load flagged references from flagged_file_path.
    2) For each flagged reference:
       - Attempt CVF search
       - If not found, attempt NeurIPS search
       - If not found, attempt OpenReview search
       - If not found in any => mark as 'bad' 
    3) Write truly unresolvable references to bad_file_path.
    """
    flagged_entries = load_flagged_references(flagged_file_path)
    bad_references = []

    for ref_id, ref_title in flagged_entries:
        if not ref_title:
            # Could not parse a title from the line, mark it as bad
            msg = f"{ref_id}: Missing or unparseable title in flagged_references."
            logging.debug(msg)
            bad_references.append(msg)
            continue

        # Attempt CVF => if found, done
        found = search_cvf(ref_title)
        if not found:
            # Attempt NeurIPS => if found, done
            found = search_neurips(ref_title)

        if not found:
            # Attempt OpenReview => if found, done
            found = search_openreview(ref_title)

        if not found:
            msg = f"{ref_id}: Not found on CVF / NeurIPS / OpenReview => '{ref_title}'"
            logging.debug(msg)
            bad_references.append(msg)

    # Write final "bad references" to disk
    with open(bad_file_path, "w", encoding="utf-8") as fout:
        for item in bad_references:
            fout.write(item + "\n")

    if not bad_references:
        print("No 'bad references' found. All flagged references were matched somewhere!")
    else:
        print("Some references remain unverified. See bad_references.txt.")
        for item in bad_references:
            print(" -", item)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python check_flagged.py path/to/flagged_references.txt [output_bad_file.txt]")
        sys.exit(1)

    flagged_file = sys.argv[1]
    bad_file = sys.argv[2] if len(sys.argv) >= 3 else "bad_references.txt"
    main(flagged_file, bad_file)


