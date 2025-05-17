# StaticSite
# File: build.py
# Purpose: Read configuration file and build static website
# Created: December 16, 2024
# Modified: May 17, 2025

import os
import json
import logging
import markdown
import minify_html
import pathlib
import shutil
import argparse

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, SkipValidation
from typing import Any, Union, Literal, Annotated, Dict, List
from lysine import Environment, FileSystemLoader, select_autoescape
from PIL import Image

class Page(BaseModel):
    # Page title displayed in <title> and other places
    title: str
    # Page theme
    theme: str
    # Optional: specify a template to override the default HTML template in a given theme (main.lis)
    htmloverride: str = "main.lis"
    # Optional: specify a template to override the default CSS template in a given theme (main.lis)
    cssoverride: str = "main.lis"
    # Optional: starting date of the page
    startdate: Union[str, None] = None
    # Optional: ending date of the page, e.g. not used for blogs
    enddate: Union[str, None] = None
    # Page description
    desc: str
    # Optional: list of scripts to include in the page
    scripts: List[str] = []
    # Point to a markdown file to render
    content: str

class SiteConfig(BaseModel):
    sitedomain: str
    # Minify HTML
    minifyhtml: bool = True
    # Minify CSS
    minifycss: bool = True
    # Generate images from .drawio files
    drawio: bool = True
    # Export file format
    drawiofmt: Literal["svg", "png", "jpg", "webp"] = "png"
    # Export image scale (bitmap formats only)
    drawioscale: int = 1
    # Enable thumbnailing
    thumbnails: bool = True
    # Thumbnail size
    thumbsize: int = 600
    # Thumbnail resize algorithm
    thumbalgo: Literal["nearest", "lanczos", "bilinear", "bicubic", "box", "hamming"] = "nearest"
    # Generate sitemap.xml
    sitemap: bool = True
    # Date formats to use in page buttons
    dateformats: Dict[str, str]
    # TODO: Value str should be an enum
    staticexts: Dict[str, str]
    # Theme name: template directory
    themes: Dict[str, str]

logger = logging.getLogger(__name__)
logger.setLevel(logging.CRITICAL)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(handler)

parser = argparse.ArgumentParser(description = "Static website generator", prog = "StaticSite")
parser.add_argument("configdir", help = "Configuration (input) directory", type = str)
parser.add_argument("outputdir", help = "Output directory", type = str)
parser.add_argument("-l", "--loglevel", choices = ["info", "debug", "warning", "error", "fatal"], default = "info", help = "Verbosity level")
parser.add_argument("-e", "--skiperrors", help = "Skip errors", type = bool, default = True)

args = parser.parse_args()
cfgdir = args.configdir

try:
    with open(cfgdir + "/config.json", "r") as f:
        configjson = f.read()
    siteconfigdata = json.loads(configjson)
    siteconfig: SiteConfig = SiteConfig(**siteconfigdata)
except Exception as e:
    logger.fatal(f"Could not read config.json {e}")
    exit(1)

if not os.path.exists(cfgdir):
    logger.fatal(f"Configuration directory {cfgdir} does not exist")
    exit(1)
elif not os.path.isdir(cfgdir):
    logger.fatal(f"{cfgdir} is not a directory")
    exit(1)

outdir = args.outputdir
if not os.path.exists(outdir):
    logger.fatal(f"Output directory {outdir} does not exist")
    exit(1)
elif not os.path.isdir(outdir):
    logger.fatal(f"{outdir} is not a directory")
    exit(1)

loglevel = args.loglevel

if loglevel == "debug":
    logger.setLevel(logging.DEBUG)
elif loglevel == "info":
    logger.setLevel(logging.INFO)
elif loglevel == "warning":
    logger.setLevel(logging.WARNING)
elif loglevel == "error":
    logger.setLevel(logging.ERROR)
elif loglevel == "critical":
    logger.setLevel(logging.CRITICAL)
elif loglevel == "fatal":
    logger.setLevel(logging.CRITICAL)

skiperrors = args.skiperrors
thumbsize = siteconfig.thumbsize

thumbalgocfg = siteconfig.thumbalgo
if thumbalgocfg == "nearest":
    thumbalgo = Image.Resampling.NEAREST
elif thumbalgocfg == "lanczos":
    thumbalgo = Image.Resampling.LANCZOS
elif thumbalgocfg == "bilinear":
    thumbalgo = Image.Resampling.BILINEAR
elif thumbalgocfg == "bicubic":
    thumbalgo = Image.Resampling.BICUBIC
elif thumbalgocfg == "box":
    thumbalgo = Image.Resampling.BOX
elif thumbalgocfg == "hamming":
    thumbalgo = Image.Resampling.HAMMING

configjsonpath = pathlib.Path(cfgdir) / "config.json"
def findpages() -> Dict[str, Page]:
    pages: Dict[str, Page] = {}

    for pagedir in os.walk(os.path.join(cfgdir, "pages")):
        if ".ignore" in pagedir[2]:
            logger.debug(f".ignore found in {pagedir[0]}, skipping")
            continue

        if not "page.json" in pagedir[2]:
            logger.warn(f"page.json not found in {pagedir[0]}, skipping")
            continue

        with open(os.path.join(pagedir[0], "page.json"), "r") as f:
            pagejsonraw = f.read()

            try:
                pagejson: Dict = json.loads(pagejsonraw)
                page: Page = Page(**pagejson)
            except Exception as e:
                logger.error(f"Could not parse page.json in {pagedir[0]}: {e}")
                continue

    return pages

def buildpages(pages: Dict[str, Page]):
    for page in pages.keys():
        logger.info(f"Building page {page}")

# Only thumbnail images from pages that were registered
def thumbnails(pages: Dict[str, Page]):
    for page in pages.keys():
        for files in os.listdir():
            suffix = pathlib.Path(f).suffix
            nosuffix = pathlib.Path(f).stem

            if siteconfig.staticexts[suffix] != "image":
                continue

            img = Image.open(os.path.join(dirs[0], f))
            if img.size[1] <= thumbsize:
                continue

            hratio = (thumbsize / float(img.size[1]))
            wsize = int((float(img.size[0]) * float(hratio)))

            logger.debug(f"Resizing {f} to {wsize}x{thumbsize}")
            img = img.resize((wsize, thumbsize), Image.Resampling.BICUBIC)

            img.save(os.path.join(dirs[0], f"{nosuffix}_thumb{suffix}"))


def drawio():
    staticdirs = [x for x in os.walk(os.path.join(outdir, "static/pages"))]
    for dirs in staticdirs:
        for f in dirs[2]:
            suffix = pathlib.Path(f).suffix
            nosuffix = pathlib.Path(f).stem

            if siteconfig.staticexts[suffix] != "drawio":
                continue

            imgpath = os.path.join(dirs[0], f)

            logger.debug(f"Converting {imgpath}")
            # TODO: Add Windows support if it is ever needed
            stat = os.system(f"drawio -x -o {dirs[0]}/ -f {siteconfig.drawiofmt} -s {siteconfig.drawioscale} {imgpath} -b 8 -s 2 1>/dev/null 2>/dev/null")
            if stat != 0:
                logger.error(f"Error converting {imgpath}")
                continue


def buildcss():
    templates: Dict[str, Environment] = {}
    for templatedir in [x for x in os.listdir(os.path.join(cfgdir, "templates"))]:
        templates[templatedir] = Environment(
            loader = FileSystemLoader(os.path.join(cfgdir, "templates", templatedir, "css")),
            autoescape = select_autoescape()
        )

    os.makedirs(os.path.join(outdir, "static/styles"))

    for name, theme in siteconfig.themes.items():
        themepath = os.path.join(outdir, f"static/styles/{name}.css")

        template = templates[theme].get_template("main.lis")
        css = template.render(theme = theme, themes = siteconfig.themes)

        if siteconfig.minifycss:
            css = minify_html.minify(css)

        with open(themepath, "w") as f:
            f.write(css)

def sitemap(pages: Dict[str, Page]):
    sitemap_urls = []
    for pagepath, pageconfig in pages.items():
        sitemap_urls.append(f"<url><loc>https://{siteconfig.sitedomain}{pagepath}</loc></url>\n")

    sitemap = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    {}</urlset>""".format("".join(sitemap_urls))

    with open(os.path.join(outdir, "sitemap.xml"), "w") as f:
        f.write(sitemap)


if __name__ == "__main__":
    pages: Dict[str, Page] = findpages()

    buildpages(pages)

    if siteconfig.thumbnails:
        thumbnails(pages)

    if siteconfig.drawio:
        drawio()

    buildcss()

    if siteconfig.sitemap:
        sitemap(pages)

    # Copy static files
    if os.path.exists(os.path.join(outdir, "static")):
        shutil.rmtree(os.path.join(outdir, "static"))
    shutil.copytree(os.path.join(cfgdir, "static"), os.path.join(outdir, "static"))

    # Copy robots.txt
    if os.path.exists(os.path.join(outdir, "robots.txt")):
        os.remove(os.path.join(outdir, "robots.txt"))
    shutil.copy(os.path.join(cfgdir, "robots.txt"), os.path.join(outdir, "robots.txt"))

