"""Tile the location gallery into one labelled contact sheet.

Ten separate files are hard to judge as a set and awkward to show a client.
"""

import json
import pathlib

import locations
import montage

MANIFEST = pathlib.Path('assets/locations/gallery.json')
OUTPUT = pathlib.Path('out/location-contact-sheet.jpg')


def main() -> None:
    manifest = json.loads(MANIFEST.read_text())
    # India first, then international, so the sheet reads the way the picker will.
    items = [(manifest[key]['label'], manifest[key]['file'])
             for key in locations.ALL if key in manifest]
    print(montage.build(items, OUTPUT, columns=5))


if __name__ == '__main__':
    main()
