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
     - If found, parse pub.bibtex for the official "Cite" BibTeX.
       * Merge only missing fields into your original. 
         If a mismatch is detected, log it but keep your original field.
       * If no official BibTeX, partially fill from pub.bib but again preserve your original.
     - If no match is found, keep the original as-is.
  4) Writes partial results after each entry to avoid losing progress.
  5) After all are processed, shows a unified diff of how the .bib was changed.

Dependencies:
  - scholarly (>= 1.7.11)
  - bibtexparser
  - python-Levenshtein + thefuzz (if you want fuzzy features, but not used here)

Notes:
  - If you pass an inexact or ambiguous title, search_single_pub might return
    the "wrong" paper or None. You can adapt as needed.
  - Increase DELAY_BETWEEN_QUERIES if you experience blocks.
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
DELAY_BETWEEN_QUERIES = 15    # Seconds to sleep between references
# -----------------------------


def load_existing_output_db(output_path: str) -> BibDatabase:
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
    # We don't reorder entries; they stay in the order appended
    writer.order_entries_by = None
    bib_str = bibtexparser.dumps(db, writer)
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(bib_str)
    print(f"  [INFO] Wrote partial/final results to '{outfile}'")


def parse_official_bibtex(raw_bibtex: str, original_id: str, original_type: str) -> dict:
    """
    Given a raw BibTeX string from 'pub.bibtex',
    parse it with bibtexparser and return a dictionary representing
    the single entry, preserving the original ID and ENTRYTYPE.
    """
    new_entry = {}
    try:
        temp_db = bibtexparser.loads(raw_bibtex)
        if temp_db.entries:
            new_entry = temp_db.entries[0]
        else:
            print("  [WARN] Official BibTeX was parsed but contained no entries.")
    except Exception as e:
        print(f"  [ERROR] Could not parse official BibTeX: {e}")
        return {}

    # Force ID and type to match the original
    new_entry["ID"] = original_id
    new_entry["ENTRYTYPE"] = original_type
    return new_entry


def merge_entries_preserving_original(original: dict, official: dict) -> dict:
    """
    Merge fields from 'official' into 'original', but preserve
    anything the original already has. If there's a mismatch,
    we log a warning but keep the original field.

    Return a new dictionary representing the final merged entry.
    """
    merged = dict(original)  # Copy so we don't mutate the caller

    for field, off_value in official.items():
        # Skip special fields
        if field in ["ID", "ENTRYTYPE"]:
            continue

        orig_value = merged.get(field, "")
        if not orig_value and off_value:
            # Fill missing field
            merged[field] = off_value
        elif orig_value and off_value and (orig_value.strip() != off_value.strip()):
            # There's a mismatch. Log it, but keep the original
            print(f"    [MISMATCH] Field '{field}':")
            print(f"      Original: {orig_value}")
            print(f"      Official: {off_value}")
            print(f"    Keeping original field value.")

    return merged


def merge_partial_pub_data(original: dict, partial_bib: dict) -> dict:
    """
    If 'official' BibTeX is not available, we might only have partial pub.bib data.
    Merge it similarly to merge_entries_preserving_original.
    Return the merged dict.
    """
    merged = dict(original)  # Copy so we don't mutate

    # We only handle a few commonly used fields from 'pub.bib'
    possible_fields = ["title", "author", "year", "venue", "abstract"]
    for field in possible_fields:
        pub_val = partial_bib.get(field, "")
        if not pub_val:
            continue

        if field == "venue":
            # Attempt to guess if this is a conference or journal
            # but preserve original if there's a mismatch
            original_venue = merged.get("booktitle") or merged.get("journal") or ""
            if not original_venue and pub_val:
                if any(token in pub_val.lower() for token in ["conf", "conference", "proc", "workshop"]):
                    merged["booktitle"] = pub_val
                else:
                    merged["journal"] = pub_val
            elif original_venue and pub_val and (original_venue.strip() != pub_val.strip()):
                print(f"    [MISMATCH] Original venue: {original_venue} vs partial 'venue': {pub_val}")
                print("    Keeping original venue.")
        elif field == "author":
            orig_authors = merged.get("author", "")
            # If there's no author in original, fill it
            if not orig_authors and pub_val:
                # pub_val might be a string or list. If list, join with ' and '
                if isinstance(pub_val, list):
                    pub_val = " and ".join(pub_val)
                merged["author"] = pub_val
            elif orig_authors and pub_val and (orig_authors.strip() != pub_val.strip()):
                print(f"    [MISMATCH] Original authors: {orig_authors} vs partial: {pub_val}")
                print("    Keeping original authors.")
        else:
            # normal field (title, year, abstract) 
            original_val = merged.get(field, "")
            if not original_val and pub_val:
                merged[field] = pub_val
            elif original_val and pub_val and (original_val.strip() != pub_val.strip()):
                print(f"    [MISMATCH] Field '{field}':")
                print(f"      Original: {original_val}")
                print(f"      Partial: {pub_val}")
                print("    Keeping original field value.")

    return merged


def main():
    parser = argparse.ArgumentParser(description="Validate BibTeX references via Google Scholar.")
    parser.add_argument("--input", "-i", required=True, help="Path to the input .bib file.")
    parser.add_argument("--output", "-o", required=True, help="Path to the corrected .bib file.")
    args = parser.parse_args()

    # (Optional) Set up free proxy to reduce blocking
    if USE_FREE_PROXY:
        print("[INFO] Attempting to set up free proxies with ProxyGenerator()...")
        pg = ProxyGenerator()
        if pg.FreeProxies():
            scholarly.use_proxy(pg)
            print("[INFO] Successfully using free proxies.")
        else:
            print("[WARN] Could not set up free proxies. Proceeding without proxy.")

    # 1) Load original .bib
    if not os.path.isfile(args.input):
        print(f"[ERROR] Input file '{args.input}' not found.")
        sys.exit(1)
    with open(args.input, "r", encoding="utf-8") as bibfile:
        original_db = bibtexparser.load(bibfile)
    total_entries = len(original_db.entries)

    # 2) Load existing output (partial progress)
    existing_db = load_existing_output_db(args.output)
    processed_ids = {e["ID"] for e in existing_db.entries if "ID" in e}

    # We'll build our final corrected DB from what's already in output
    corrected_db = BibDatabase()
    corrected_db.entries = list(existing_db.entries)

    print(f"[INFO] Found {total_entries} entries in '{args.input}'")
    print(f"[INFO] Already processed: {len(processed_ids)} entries in '{args.output}'")

    # 3) Process each entry if not already processed
    for idx, entry in enumerate(original_db.entries):
        entry_id = entry.get("ID", f"UNKNOWN_{idx}")
        entry_type = entry.get("ENTRYTYPE", "misc")

        if entry_id in processed_ids:
            print(f"\n=== Skipping already-processed entry '{entry_id}' ===")
            continue

        print(f"\n=== Processing entry '{entry_id}' (#{idx+1}/{total_entries}) ===")

        # We'll rely on the "title" field for search_single_pub
        title = entry.get("title", "").strip()
        if not title:
            print(f"  [WARN] No title in entry '{entry_id}'. Keeping original.")
            corrected_db.entries.append(entry)
            processed_ids.add(entry_id)
            write_bib_to_disk(corrected_db, args.output)
            time.sleep(DELAY_BETWEEN_QUERIES)
            continue

        # Attempt to retrieve single best match from Google Scholar
        try:
            pub = scholarly.search_single_pub(title, filled=True)
        except MaxTriesExceededException as e:
            print(f"[ERROR] Scholarly blocked us: {e}")
            print("[ERROR] Saving partial results and stopping.")
            write_bib_to_disk(corrected_db, args.output)
            sys.exit(1)
        except Exception as e:
            print(f"[ERROR] Unexpected error searching for '{title}': {e}")
            print("[ERROR] Saving partial results and stopping.")
            write_bib_to_disk(corrected_db, args.output)
            sys.exit(1)

        if pub is None:
            # No match found
            print("  [WARN] No match found. Keeping original.")
            corrected_db.entries.append(entry)
        else:
            # If the official BibTeX was found (pub.bibtex)
            if hasattr(pub, "bibtex") and pub.bibtex:
                print("  [INFO] Found official BibTeX from 'Cite' button.")
                official_bib = parse_official_bibtex(pub.bibtex, entry_id, entry_type)

                if official_bib:
                    # Merge official fields into original, but keep original on mismatch
                    merged_entry = merge_entries_preserving_original(original=entry, official=official_bib)
                    corrected_db.entries.append(merged_entry)
                else:
                    print("  [WARN] Could not parse official bib. Keeping original.")
                    corrected_db.entries.append(entry)
            else:
                # No official bibtex => fallback to partial data in pub.bib
                print("  [WARN] No official BibTeX. Fallback to partial pub.bib.")
                partial_bib = {}
                if hasattr(pub, "bib"):
                    partial_bib = pub.bib
                elif isinstance(pub, dict):
                    partial_bib = pub.get("bib", {})

                if partial_bib:
                    merged_entry = merge_partial_pub_data(original=entry, partial_bib=partial_bib)
                    corrected_db.entries.append(merged_entry)
                else:
                    print("  [WARN] No partial data found. Keeping original.")
                    corrected_db.entries.append(entry)

        # Mark this entry as processed, save partial results
        processed_ids.add(entry_id)
        write_bib_to_disk(corrected_db, args.output)
        time.sleep(DELAY_BETWEEN_QUERIES)

    # 4) After processing all, print a unified diff of final vs. original
    print("\n[INFO] Finished processing all entries successfully.")
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

