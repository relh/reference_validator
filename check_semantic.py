import re
import requests
import time
from bs4 import BeautifulSoup

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode

from urllib.parse import quote
from difflib import SequenceMatcher

DEBUG_MODE = True  # Toggle debug logs on/off
SEMANTIC_SCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
BAD_OUTPUTS_LOG_FILENAME = "bad_references.log"

def debug_print(msg):
    if DEBUG_MODE:
        print(msg)

def similarity(a, b):
    """
    Returns a float in [0, 1] indicating how similar two strings are,
    using Python's built-in SequenceMatcher.
    """
    return SequenceMatcher(None, a, b).ratio()

def fetch_arxiv_title(arxiv_id):
    """
    Fetches the official title from arXiv.org by the given arXiv ID (e.g., '1212.0402').
    Returns None if error or unable to parse.
    """
    url = f"https://arxiv.org/abs/{arxiv_id}"
    debug_print(f"  -> Fetching title from: {url}")
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        debug_print(f"[Error] Could not fetch {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    meta_tag = soup.find("meta", attrs={"name": "citation_title"})
    if meta_tag and meta_tag.get("content"):
        title = meta_tag["content"].strip()
        debug_print(f"     Found arXiv title: {title}")
        return title
    else:
        debug_print(f"     [Warning] No 'citation_title' found in the page.")
        return None

def parse_arxiv_id_from_text(value):
    """
    If `value` contains something like 'arXiv:xxxx.xxxx' or
    'arxiv.org/abs/xxxx.xxxx', extract and return that ID.
    Otherwise return None.
    """
    match = re.search(r'arxiv\.org/abs/([\w\.\-v]+)', value, re.IGNORECASE)
    if match:
        return match.group(1)

    match = re.search(r'arxiv\s*:\s*([\w\.\-v]+)', value, re.IGNORECASE)
    if match:
        return match.group(1)

    return None

def load_bib_entries(bib_path):
    """
    Loads a .bib file using bibtexparser, returning a list of dicts,
    where each dict has all the BibTeX fields (lowercased keys).
    """
    debug_print(f"Loading .bib file: {bib_path}")
    parser = BibTexParser()
    parser.customization = convert_to_unicode

    with open(bib_path, encoding="utf-8") as f:
        bib_database = bibtexparser.load(f, parser=parser)

    debug_print(f"Total entries loaded: {len(bib_database.entries)}")
    return bib_database.entries

def semantic_scholar_search(title, max_results=3):
    """
    Queries Semantic Scholar's search API for a given title.
    Returns a list of top results, each being a dict with keys:
      {
         'paperId': ...,
         'title': ...,
         'year':  ...,
         ...
      }
    or an empty list if no results found or an error occurs.
    """
    if not title:
        return []

    query = quote(title)
    url = f"{SEMANTIC_SCHOLAR_API_URL}?query={query}&limit={max_results}"

    debug_print(f"  -> Searching Semantic Scholar for: '{title}'")
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get('data', [])
    except requests.RequestException as e:
        debug_print(f"[Error] Could not fetch from Semantic Scholar: {e}")
        return []
    except ValueError as e:
        debug_print(f"[Error] Could not parse JSON: {e}")
        return []

def check_paper_existence(title, threshold=0.8, max_results=3):
    """
    Checks if a paper with the given 'title' likely exists in the
    Semantic Scholar database by searching & comparing titles.
    Returns True if any result is similar >= threshold, else False.
    """
    results = semantic_scholar_search(title, max_results=max_results)

    # Sleep 5 seconds to avoid 429 (rate-limiting)
    time.sleep(5)

    if not results:
        return False

    for r in results:
        r_title = r.get("title", "")
        sim = similarity(r_title.lower(), title.lower())
        debug_print(f"     -> Candidate: {r_title} | similarity={sim:.2f}")

        if sim >= threshold:
            return True
    return False

def main(bib_file_path):
    entries = load_bib_entries(bib_file_path)
    if not entries:
        print("[Warning] No entries found in the .bib file.")
        return

    mismatch_found = False
    check_existence_enabled = True

    # We'll keep a list of "potentially bad" references to log later
    bad_entries = []

    for idx, entry in enumerate(entries, start=1):
        key = entry.get('id')  # The citation key
        title = entry.get('title', "")
        archiveprefix = entry.get('archiveprefix', "")
        eprint_val = entry.get('eprint', "")
        booktitle = entry.get('booktitle', "")
        url = entry.get('url', "")

        debug_print(f"\n[{idx}] Key={key} | Title='{title}'")

        # 1) Attempt to detect an arXiv ID
        if archiveprefix.lower() == 'arxiv' and eprint_val:
            arxiv_id = eprint_val
            debug_print(f"     -> Found 'archivePrefix=arXiv' and 'eprint={arxiv_id}'")
        else:
            arxiv_id = None
            # Check common fields for an arXiv pattern
            for field_val in [booktitle, url, title]:
                possible_id = parse_arxiv_id_from_text(field_val)
                if possible_id:
                    arxiv_id = possible_id
                    debug_print(f"     -> Found possible arXiv ID '{arxiv_id}' in '{field_val}'")
                    break

        # 2) If we found an arXiv ID, fetch official title
        if arxiv_id:
            official_title = fetch_arxiv_title(arxiv_id)
            # Sleep to avoid too many requests in a short time
            time.sleep(5)

            if official_title:
                # Compare with local BibTeX 'title'
                if title and official_title.lower().strip() != title.lower().strip():
                    mismatch_found = True
                    mismatch_msg = (
                        f"[Title Mismatch] Entry key: {key}\n"
                        f"  ► BibTeX title: {title}\n"
                        f"  ► ArXiv title:  {official_title}\n"
                    )
                    print(mismatch_msg)
                    bad_entries.append(mismatch_msg)
        else:
            debug_print("     -> No recognized arXiv ID in this entry.")

        # 3) (Optional) Check if the paper “really exists” using Semantic Scholar
        if check_existence_enabled and title.strip():
            found_in_semantic_scholar = check_paper_existence(title, threshold=0.75)
            if not found_in_semantic_scholar:
                warning_msg = (
                    f"[Existence Warning] Could not confirm paper '{title}' (key: {key}) "
                    "on Semantic Scholar.\n"
                    "  -> Possibly not indexed or the title differs significantly.\n"
                )
                print(warning_msg)
                bad_entries.append(warning_msg)

    print("\nFinished checking references.")
    if mismatch_found:
        print("Some references appear to have mismatched titles.")
    else:
        print("No mismatched arXiv titles found.")

    # 4) Write out the "potential bad" references to a file
    if bad_entries:
        with open(BAD_OUTPUTS_LOG_FILENAME, "w", encoding="utf-8") as f:
            for line in bad_entries:
                f.write(line + "\n")
        print(f"\nWrote {len(bad_entries)} 'bad' lines to {BAD_OUTPUTS_LOG_FILENAME}")
    else:
        print("\nNo 'bad' references recorded.")

if __name__ == "__main__":
    main("references.bib")

