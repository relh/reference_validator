#!/usr/bin/env python3
"""
validate.py

Usage:
  python validate.py --input references.bib --output clean_references.bib

Description:
  1) (Optional) Sets up free proxies via scholarly.ProxyGenerator.
  2) Loads any existing output .bib to skip references already processed.
  3) For each unprocessed reference in the input .bib:
     - Extract the exact title field (string).
     - Call scholarly.search_single_pub(title, filled=True) to retrieve a single best match.
       * This also runs pub.fill() under the hood, attempting to retrieve an official "Cite" bibtex.
     - If found, parse the official BibTeX to create a new entry.
       * Otherwise, fallback to partial data (pub.bib).
  4) Writes partial results after each entry to avoid losing progress.
  5) If all entries are processed, shows a unified diff of how the .bib was changed.

Dependencies:
  - scholarly (>= 1.7.11)
  - bibtexparser
  - python-Levenshtein + thefuzz (if you want fuzzy features, but this script doesn't use them)

Caveat:
  - If you pass an inexact or ambiguous title, search_single_pub might return the "wrong" paper
    or might return None if it cannot find an exact match. You can adapt as needed.

Recommended:
  - Use smaller sets of references if you experience blocks.
  - Increase the wait time if you get blocked frequently.
"""

import os
import sys
import time
import difflib
import argparse

import bibtexparser
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.bibdatabase import BibDatabase

from scholarly import scholarly, ProxyGenerator
from scholarly._proxy_generator import MaxTriesExceededException


# -----------------------------
# Adjustable Parameters/Defaults
# -----------------------------
USE_FREE_PROXY = False       # Toggle to attempt free proxies
DELAY_BETWEEN_QUERIES = 5    # Seconds to sleep between references
# -----------------------------


def load_existing_output_db(output_path):
    """
    If 'output_path' exists, parse it into a BibDatabase.
    Otherwise return an empty database.
    """
    if os.path.isfile(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            return bibtexparser.load(f)
    else:
        return BibDatabase()


def write_bib_to_disk(db: BibDatabase, outfile: str):
    """
    Serialize the given BibDatabase 'db' to 'outfile' with bibtexparser.
    """
    writer = BibTexWriter()
    writer.order_entries_by = None  # Keep the order we append
    bib_str = bibtexparser.dumps(db, writer)
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(bib_str)
    print(f"  [INFO] Wrote partial/final results to '{outfile}'")


def parse_official_bibtex(raw_bibtex: str, original_id: str, original_type: str) -> dict:
    """
    Given a raw BibTeX string from search_single_pub(..., filled=True),
    parse it with bibtexparser. Return a dict representing the single entry,
    preserving the original ID and ENTRYTYPE from our .bib file.
    """
    new_entry = {}
    try:
        temp_db = bibtexparser.loads(raw_bibtex)
        if temp_db.entries:
            # Typically only one entry from a single pub
            new_entry = temp_db.entries[0]
        else:
            print("  [WARN] Official BibTeX was parsed but contained no entries.")
    except Exception as e:
        print(f"  [ERROR] Could not parse official BibTeX: {e}")
        return {}

    # Overwrite ID and type with the original
    new_entry["ID"] = original_id
    new_entry["ENTRYTYPE"] = original_type
    return new_entry


def main():
    parser = argparse.ArgumentParser(description="Validate BibTeX references using search_single_pub(..., filled=True).")
    parser.add_argument("--input", "-i", required=True, help="Path to the input .bib file.")
    parser.add_argument("--output", "-o", required=True, help="Path to the corrected .bib file.")
    args = parser.parse_args()

    # Optional: set up free proxy to reduce blocking
    if USE_FREE_PROXY:
        print("[INFO] Attempting to set up free proxies with ProxyGenerator()...")
        pg = ProxyGenerator()
        if pg.FreeProxies():
            scholarly.use_proxy(pg)
            print("[INFO] Successfully using free proxies.")
        else:
            print("[WARN] Could not set up free proxies. Proceeding without proxy.")

    # 1) Load the original .bib
    if not os.path.isfile(args.input):
        print(f"[ERROR] Input file '{args.input}' not found.")
        sys.exit(1)
    with open(args.input, "r", encoding="utf-8") as bibfile:
        original_db = bibtexparser.load(bibfile)

    # 2) Load existing output
    existing_db = load_existing_output_db(args.output)
    processed_ids = {e["ID"] for e in existing_db.entries if "ID" in e}

    # Build our final corrected DB starting with what's already in output
    corrected_db = BibDatabase()
    corrected_db.entries = list(existing_db.entries)

    total_entries = len(original_db.entries)
    print(f"[INFO] Found {total_entries} entries in '{args.input}'")
    print(f"[INFO] Already processed: {len(processed_ids)} entries in '{args.output}'")

    # 3) Loop over all entries in the input
    for idx, entry in enumerate(original_db.entries):
        entry_id = entry.get("ID", f"UNKNOWN_{idx}")
        if entry_id in processed_ids:
            print(f"\n=== Skipping already-processed entry '{entry_id}' ===")
            continue

        print(f"\n=== Processing entry '{entry_id}' (index {idx}) ===")

        # We rely on the "title" field for search_single_pub
        title = entry.get("title", "").strip()
        if not title:
            print(f"  [WARN] No title in entry '{entry_id}'. Keeping original.")
            corrected_db.entries.append(entry)
            processed_ids.add(entry_id)
            write_bib_to_disk(corrected_db, args.output)
            time.sleep(DELAY_BETWEEN_QUERIES)
            continue

        # Attempt to retrieve a single best match from Google Scholar
        try:
            # If the "title" is fairly accurate, we might get a perfect match
            # search_single_pub attempts to fill the result automatically
            # i.e., no need to separately call pub.fill().
            pub = scholarly.search_single_pub(title, filled=True)
        except MaxTriesExceededException as e:
            print(f"[ERROR] Scholarly raised MaxTriesExceededException: {e}")
            print("[ERROR] Possibly blocked by Google Scholar. Saving partial results and stopping.")
            write_bib_to_disk(corrected_db, args.output)
            sys.exit(1)
        except Exception as e:
            print(f"[ERROR] Unexpected error searching for '{title}': {e}")
            print("[ERROR] Saving partial results and stopping.")
            write_bib_to_disk(corrected_db, args.output)
            sys.exit(1)

        if pub is None:
            # No single publication matched. Keep the original
            print(f"  [WARN] search_single_pub returned None. Keeping original entry.")
            corrected_db.entries.append(entry)
        else:
            # If the official BibTeX was found, pub.bibtex is presumably populated
            if hasattr(pub, "bibtex") and pub.bibtex:
                # This is the official BibTeX from "Cite" button
                print("  [INFO] Found official BibTeX from 'Cite' button.")
                new_entry = parse_official_bibtex(
                    raw_bibtex=pub.bibtex,
                    original_id=entry_id,
                    original_type=entry.get("ENTRYTYPE", "misc")
                )
                if new_entry:
                    corrected_db.entries.append(new_entry)
                else:
                    print("  [WARN] Official BibTeX parse failed. Keeping original entry.")
                    corrected_db.entries.append(entry)

            else:
                # No official BibTeX => fallback to partial data
                print("  [WARN] No official BibTeX. Fallback to partial data.")
                # We must check if 'pub' is a dict or an object with a .bib attribute
                if hasattr(pub, "bib"):
                    partial_bib = pub.bib
                elif isinstance(pub, dict):
                    partial_bib = pub.get("bib", {})
                else:
                    partial_bib = {}

                if partial_bib:
                    # Merge partial fields into a copy of the original
                    merged = dict(entry)
                    if "title" in partial_bib:
                        merged["title"] = partial_bib["title"]
                    if "author" in partial_bib:
                        authors = partial_bib["author"]
                        if isinstance(authors, list):
                            authors = " and ".join(authors)
                        merged["author"] = authors
                    if "year" in partial_bib:
                        merged["year"] = partial_bib["year"]

                    # Guess conference vs. journal from "venue"
                    if "venue" in partial_bib:
                        v = partial_bib["venue"].lower()
                        if any(token in v for token in ["conf", "conference", "proc", "workshop"]):
                            merged["booktitle"] = partial_bib["venue"]
                        else:
                            merged["journal"] = partial_bib["venue"]

                    corrected_db.entries.append(merged)
                else:
                    # If we couldn't get partial data, keep original
                    print("  [WARN] No partial data found either. Keeping original.")
                    corrected_db.entries.append(entry)

                corrected_db.entries.append(merged)

        # Mark as processed, write partial results
        processed_ids.add(entry_id)
        write_bib_to_disk(corrected_db, args.output)
        time.sleep(DELAY_BETWEEN_QUERIES)

    # 4) If we get here, we've processed all references
    print("\n[INFO] Finished processing all entries successfully.")
    # Print a diff of the final results
    with open(args.input, "r", encoding="utf-8") as orig_f:
        original_bib_txt = orig_f.read()
    with open(args.output, "r", encoding="utf-8") as new_f:
        new_bib_txt = new_f.read()

    diff = difflib.unified_diff(
        original_bib_txt.splitlines(keepends=True),
        new_bib_txt.splitlines(keepends=True),
        fromfile=args.input,
        tofile=args.output
    )
    diff_text = "".join(diff)
    if diff_text.strip():
        print("\n=== Diff between original and corrected .bib files ===")
        print(diff_text)
    else:
        print("\nNo changes detected between the original and corrected .bib files.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[INFO] KeyboardInterrupt. Saving partial results before exit.")
        sys.exit(1)

