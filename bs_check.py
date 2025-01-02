import re
import requests
from bs4 import BeautifulSoup

# Dedicated BibTeX parser
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode

DEBUG_MODE = True  # Toggle debug logs on/off

def debug_print(msg):
    if DEBUG_MODE:
        print(msg)

def fetch_arxiv_title(arxiv_id):
    """
    Fetches the official title from arXiv.org by given arXiv ID (e.g., '1212.0402').
    Returns None if error or unable to parse.
    """
    url = f"https://arxiv.org/abs/{arxiv_id}"
    debug_print(f"  -> Fetching title from: {url}")
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[Error] Could not fetch {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    meta_tag = soup.find("meta", attrs={"name": "citation_title"})
    if meta_tag and meta_tag.get("content"):
        title = meta_tag["content"].strip()
        debug_print(f"     Found arXiv title: {title}")
        return title
    else:
        debug_print(f"     [Warning] No 'citation_title' found in page.")
        return None

def parse_arxiv_id_from_text(value):
    """
    If `value` contains something like 'arXiv:xxxx.xxxx' or
    'arxiv.org/abs/xxxx.xxxx', extract and return that ID.
    Otherwise return None.
    """
    # Common patterns:
    #   e.g. 'arXiv:1212.0402'
    #   or   'arxiv.org/abs/1212.0402'
    # We’ll check both patterns.
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

    # Each entry is a dict with keys like: 'title', 'booktitle', 'url', etc.
    debug_print(f"Total entries loaded: {len(bib_database.entries)}")
    return bib_database.entries

def main(bib_file_path):
    entries = load_bib_entries(bib_file_path)
    if not entries:
        print("[Warning] No entries found in the .bib file.")
        return

    mismatch_found = False

    for idx, entry in enumerate(entries, start=1):
        # Because bibtexparser lowercases keys, we do entry.get('title'), entry.get('url'), ...
        key = entry.get('id')  # The citation key
        title = entry.get('title', "")
        archiveprefix = entry.get('archiveprefix', "")
        eprint_val = entry.get('eprint', "")
        booktitle = entry.get('booktitle', "")
        url = entry.get('url', "")

        debug_print(f"\n[{idx}] Key={key} | Title='{title}'")

        # 1) If there's archiveprefix=arXiv and eprint is set
        if archiveprefix.lower() == 'arxiv' and eprint_val:
            arxiv_id = eprint_val
            debug_print(f"     -> Found 'archivePrefix=arXiv' and 'eprint={arxiv_id}'")
        else:
            # 2) Otherwise see if something in booktitle or url indicates an arXiv link
            #    or even in the title if people wrote 'arXiv preprint arXiv:xxxx.xxxx' as the title
            arxiv_id = None

            for field_val in [booktitle, url, title]:
                possible_id = parse_arxiv_id_from_text(field_val)
                if possible_id:
                    arxiv_id = possible_id
                    debug_print(f"     -> Found possible arXiv ID '{arxiv_id}' in '{field_val}'")
                    break

            if not arxiv_id:
                debug_print("     -> Not recognized as an arXiv-based entry.")
                continue

        # We now have an arxiv_id
        # Compare with official arXiv title
        official_title = fetch_arxiv_title(arxiv_id)
        if not official_title:
            print(f"[Warning] Could not fetch arXiv title for key='{key}' (arXiv ID: {arxiv_id})")
            continue

        # Compare with local BibTeX 'title'. If they differ, print mismatch.
        # (Use .strip() and case-insensitive comparison.)
        if official_title.lower().strip() != title.lower().strip():
            mismatch_found = True
            print(f"\n[Title Mismatch] Entry key: {key}")
            print(f"  ► BibTeX title: {title}")
            print(f"  ► ArXiv title:  {official_title}")

    if not mismatch_found:
        print("\nNo mismatches found.")
    print("[Done]")


if __name__ == "__main__":
    # Change "my_references.bib" to the path of your .bib file
    main("references.bib")

