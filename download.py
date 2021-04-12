#!/usr/bin/python3

import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
import xml.etree.ElementTree as ET
import shutil


def fetch(url, target: Path):
    if target.exists() and target.stat().st_size > 0:
        print(f"Skipped: {url}")
        return target

    try:
        print(f"Downloading: {url}")
        with urllib.request.urlopen(url) as rs:
            os.makedirs(target.parent, exist_ok=True)
            with open(target, "wb") as fp:
                shutil.copyfileobj(rs, fp)
        return target
    except urllib.error.HTTPError as e:
        print(f"Failed: {url} ({e})")
        return
    except KeyboardInterrupt:
        if target.exists():
            target.unlink()
        raise


def getMeetingId(url):
    for pattern in (
        r"^.*/playback/presentation/2\.0/playback.html\?meetingId=(\S+)$",
        r"^.*/playback/presentation/2.3/(\S+)$",
    ):
        m = re.match(pattern, url)
        if m:
            return m.group(1)
    raise ValueError(f"Unsupported presentation URL: {url}")


def download(url, outputPath: Path):
    meetingId = getMeetingId(url)
    base = urllib.parse.urljoin(url, f"/presentation/{meetingId}/")

    def sfetch(name):
        return fetch(urllib.parse.urljoin(base, name), outputPath / name)

    sfetch("metadata.xml")
    sfetch("shapes.svg")

    with open(outputPath / "shapes.svg", "rb") as fp:
        shapes = ET.parse(fp)
        for img in shapes.iterfind(".//{http://www.w3.org/2000/svg}image"):
            sfetch(img.get("{http://www.w3.org/1999/xlink}href"))

    sfetch("panzooms.xml")
    sfetch("cursor.xml")
    sfetch("deskshare.xml")
    sfetch("presentation_text.json")
    sfetch("captions.json")
    sfetch("slides_new.xml")
    sfetch("video/webcams.webm")
    sfetch("deskshare/deskshare.webm")


if __name__ == "__main__":
    download(sys.argv[1], Path(sys.argv[2]))
