#!/usr/bin/env python3
"""
validate_bib.py

Parses a BibTeX file, queries Google Scholar for each reference using
the 'scholarly' library, and tries to fix or validate the entry’s metadata.
Results are written to 'clean_references.bib' with changes only where
mismatches are found. References that do not match anything on Scholar
are flagged.

Usage:
    python validate_bib.py --bibfile references.bib

Dependencies:
    pip install scholarly bibtexparser difflib
"""

import time
import difflib
import argparse
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode
from scholarly import scholarly

def find_best_scholar_match(entry, max_results=3):
    """
    Search Google Scholar for a given BibTeX entry (by title).
    Return the 'best' match’s metadata if any results are found, else None.

    :param entry: A dictionary representing the parsed BibTeX entry.
    :param max_results: How many search results to retrieve before we pick the first 'reasonable' match.
    :return: A dictionary with 'title', 'author', 'year', 'venue' (if found), or None if no match.
    """
    title = entry.get("title", "")
    if not title:
        return None

    # Attempt a search by title.
    search_query = scholarly.search_pubs(title)

    # Pull the first few results and see if one matches well enough.
    # The logic here is simplistic. You might want to do fuzzy matching, 
    # checking author overlap, year alignment, etc.
    for i in range(max_results):
        try:
            pub = next(search_query)
        except StopIteration:
            break  # No more results

        # The 'bib' field in the publication object typically has keys 
        # like 'title', 'author', 'pub_year', 'venue', etc.
        pub_bib = pub.bib
        # Basic check: if the titles are close enough, consider it a match.
        # We'll do a simple ratio-based match. You could expand with fuzzy matching libraries.
        ratio = difflib.SequenceMatcher(None, title.lower(), pub_bib.get("title", "").lower()).ratio()
        if ratio > 0.7:  # adjust threshold as desired
            return {
                "title": pub_bib.get("title", ""),
                "author": pub_bib.get("author", ""),
                "year": pub_bib.get("pub_year", ""),
                "venue": pub_bib.get("venue", ""),  # could be journal, booktitle, etc.
            }

    return None  # If we exhaust search results and find no good match.


def update_bib_entry(original_entry, scholar_info):
    """
    Given the original entry dict and new scholar_info dict,
    produce an updated version with corrected or added fields.

    :param original_entry: dict from BibTeX parser
    :param scholar_info: dict with 'title', 'author', 'year', 'venue'
    :return: updated dict, or None if no change
    """
    updated_entry = original_entry.copy()
    changed = False

    # Define a small helper for setting fields if they differ.
    def maybe_update(field_name, new_value):
        nonlocal changed
        old_value = updated_entry.get(field_name, "")
        # If new_value is present and meaningfully different, update and note the change.
        if new_value and (old_value.strip() != new_value.strip()):
            updated_entry[field_name] = new_value.strip()
            changed = True

    maybe_update("title", scholar_info["title"])
    maybe_update("author", scholar_info["author"])
    maybe_update("year", str(scholar_info["year"]))

    # Decide whether 'venue' should go to 'journal' or 'booktitle'.
    # This is quite heuristic-based and depends on your style rules:
    # e.g. if original has 'journal', we keep it. Otherwise, put it in 'booktitle' if it’s a conference.
    # This example is simplistic and needs your own domain logic.
    venue = scholar_info["venue"]
    if "conference" in venue.lower() or "conf" in venue.lower():
        maybe_update("booktitle", venue)
    else:
        # Could be a journal or unknown
        maybe_update("journal", venue)

    return updated_entry if changed else None


def bib_dict_to_string(entry):
    """
    Re-serialize a single entry dict to a BibTeX string snippet
    (for diffing or printing). Minimal formatting for clarity.
    """
    lines = [f"@{entry.get('type', 'misc')}{{{entry.get('id', 'NO_KEY')},"]
    for key, value in entry.items():
        if key in ["type", "id"]:
            continue
        lines.append(f"  {key} = {{{value}}},")
    lines.append("}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Validate .bib references via Google Scholar.")
    parser.add_argument("--bibfile", type=str, required=True, help="Path to your .bib file.")
    args = parser.parse_args()

    bib_filename = args.bibfile
    output_filename = "clean_references.bib"

    # Load the .bib file
    with open(bib_filename, "r", encoding="utf-8") as bib_file:
        parser = BibTexParser(customization=convert_to_unicode)
        bib_database = bibtexparser.load(bib_file, parser=parser)

    # Lists to track progress
    updated_entries = []
    failed_entries = []

    for idx, entry in enumerate(bib_database.entries):
        # Save the original for diffing
        original_string = bib_dict_to_string(entry)

        # Sleep to avoid spamming Google Scholar (1 query / ~10 sec)
        if idx > 0:
            time.sleep(10)

        # Try to find a match on Scholar
        scholar_info = find_best_scholar_match(entry)

        if not scholar_info:
            # Could not find a valid match
            failed_entries.append(entry.get('id', '(no ID)'))
            updated_entries.append(entry)  # Keep it as-is
            continue

        # Attempt updating
        new_entry = update_bib_entry(entry, scholar_info)

        if new_entry:
            # Show diff between old and new
            new_string = bib_dict_to_string(new_entry)
            diff = difflib.unified_diff(
                original_string.splitlines(keepends=True),
                new_string.splitlines(keepends=True),
                fromfile=f"{entry.get('id')}_original",
                tofile=f"{entry.get('id')}_updated",
            )
            diff_output = "".join(diff)

            if diff_output.strip():
                print("============================================================")
                print(f"DIFF for entry: {entry.get('id')}")
                print(diff_output)
                print("============================================================")

            updated_entries.append(new_entry)
        else:
            # No changes were made
            updated_entries.append(entry)

    # Update the bib_database in place with new entries
    bib_database.entries = updated_entries

    # Write out the clean references
    with open(output_filename, "w", encoding="utf-8") as out_bib:
        bibtexparser.dump(bib_database, out_bib)

    # Report on references that failed
    if failed_entries:
        print("\nThe following references did not match anything on Google Scholar:")
        for f in failed_entries:
            print(f" - {f}")
    else:
        print("\nAll references matched something on Google Scholar.")

    print(f"\nUpdated BibTeX file written to: {output_filename}")


if __name__ == "__main__":
    main()

