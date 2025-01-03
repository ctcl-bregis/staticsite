# StaticSite
# File: build.py
# Purpose: Read configuration file and build static site
# Created: December 16, 2024
# Modified: January 02, 2025

import os
import json
import logging
import markdown2
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

class LinkCustomTitle(BaseModel):
    type: Literal["titleonlycustom"]
    title: str
    theme: str
    link: str

class LinkTitle(BaseModel):
    type: Literal["titleonly"]
    page: str

# This is here to for compatibility with the old configuration format
class LinkTitleText(BaseModel):
    type: Literal["titletext"]
    text: str

class LinkFull(BaseModel):
    type: Literal["full"]
    page: str

class PageSectionLinklist(BaseModel):
    type: Literal["linklist"]
    title: str
    links: List[Annotated[Union[LinkCustomTitle, LinkTitle, LinkTitleText, LinkFull], Field(discriminator="type")]]
    # Following fields are for compatibility with the old configuration format
    # These may be removed soon
    boxed: bool
    fitscreen: bool

class PageSectionContent(BaseModel):
    type: Literal["content"]
    title: str
    content: str
    theme: str
    # Following fields are for compatibility with the old configuration format
    # These may be removed soon
    boxed: bool
    fitscreen: bool

class Page(BaseModel):
    title: str
    theme: str
    startdate: Union[str, None] = None
    enddate: Union[str, None] = None
    dateprecision: Literal["year", "day"] = "year"
    date: SkipValidation[str] = None
    desc: str
    icon: str
    icontitle: Union[str, None] = None
    content: Dict[str, Annotated[Union[PageSectionContent, PageSectionLinklist], Field(discriminator="type")]]

class Theme(BaseModel):
    dispname: str
    color: str
    fgcolor: str
    templates: str
    enabled: bool = Field(default = True)

    def __hash__(self):
        return hash((self.dispname, self.color, self.fgcolor, self.templates, self.enabled))

class SiteConfig(BaseModel):
    sitedomain: str
    dateformats: Dict[str, str]
    # TODO: Value str should be an enum
    staticexts: Dict[str, str]
    themes: Dict[str, Theme]
    #templatevars: Dict[str, Any]

def getdaterange(pageconfig: Page):
    if pageconfig.startdate:
        startdate = datetime.fromisoformat(pageconfig.startdate)
    else:
        startdate = None

    if pageconfig.enddate:
        enddate = datetime.fromisoformat(pageconfig.enddate)
    else:
        enddate = None

    if not startdate and not enddate:
        return

    if startdate and not enddate:
        if pageconfig.dateprecision == "year":
            return str(startdate.strftime(siteconfig.dateformats["year"])) + " - Present"
        elif pageconfig.dateprecision == "day":
            return str(startdate.strftime(siteconfig.dateformats["day"])) + " - Present"

    if pageconfig.dateprecision == "year" and startdate.year == enddate.year:
        return str(startdate.year)

    if pageconfig.dateprecision == "day" and startdate.year == enddate.year and startdate.month == enddate.month and startdate.day == enddate.day:
        return str(startdate.strftime(siteconfig.dateformats["day"]))

logger = logging.getLogger(__name__)
logger.setLevel(logging.FATAL)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(handler)

parser = argparse.ArgumentParser(description = "Static website generator", prog = "StaticSite")
parser.add_argument("configdir", help = "Configuration (input) directory", type = str)
parser.add_argument("outputdir", help = "Output directory", type = str)
parser.add_argument("-l", "--loglevel", choices = ["info", "debug", "warning", "error", "fatal"], default = "info", help = "Verbosity level")
parser.add_argument("-e", "--skiperrors", help = "Skip errors", type = bool, default = True)
parser.add_argument("--minify-html", help = "Minify HTML", action = "store_true")
parser.add_argument("--minify-css", help = "Minify CSS", action = "store_true")
parser.add_argument("--thumb", help = "Generate thumbnails", action = "store_true")
parser.add_argument("--thumb-size", help = "Max thumbnail height", type = int, default = 720)
parser.add_argument("--thumb-algo", help = "Thumbnail resize algorithm", choices = ["nearest", "lanczos", "bilinear", "bicubic", "box", "hamming"], default = "box")

args = parser.parse_args()
cfgdir = args.configdir
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
    logger.setLevel(logging.FATAL)

skiperrors = args.skiperrors
minifyhtml = args.minify_html
minifycss = args.minify_css
thumbnails = args.thumb
print(thumbnails)
thumbsize = args.thumb_size

thumbalgo = args.thumb_algo
if thumbalgo == "nearest":
    thumbalgo = Image.Resampling.NEAREST
elif thumbalgo == "lanczos":
    thumbalgo = Image.Resampling.LANCZOS
elif thumbalgo == "bilinear":
    thumbalgo = Image.Resampling.BILINEAR
elif thumbalgo == "bicubic":
    thumbalgo = Image.Resampling.BICUBIC
elif thumbalgo == "box":
    thumbalgo = Image.Resampling.BOX
elif thumbalgo == "hamming":
    thumbalgo = Image.Resampling.HAMMING

configjsonpath = pathlib.Path(cfgdir) / "config.json"

try:
    with open(configjsonpath, "r") as f:
        configjson = f.read()
    siteconfig = json.loads(configjson)
    siteconfig = SiteConfig(**siteconfig)
except Exception as e:
    logger.fatal(f"Could not read config.json {e}")
    exit(1)

# Render pages
templates = {}
for templatedir in [x for x in os.listdir(os.path.join(cfgdir, "templates"))]:
    templates[templatedir] = Environment(
        loader = FileSystemLoader(os.path.join(cfgdir, "templates", templatedir, "html")),
        autoescape = select_autoescape()
    )

pages = [x[0] for x in os.walk(os.path.join(cfgdir, "pages"))]
pageconfigs = {}
for page in pages:
    # This variable is to store the absolute path of the page, without the path to the configuration directory
    pageurl = page.removeprefix(cfgdir + "/pages") + "/"

    if os.path.exists(os.path.join(page, ".ignore")):
        logger.info(f"Directory {page} contains .ignore, skipping")
        continue

    if not os.path.exists(os.path.join(page, "page.json")):
        logger.error(f"Directory {page} does not contain page.json, skipping")
        continue

    pagejsonpath = os.path.join(page, "page.json")
    with open(pagejsonpath, "r") as f:
        pagejson = f.read()
        try:
            pageconfigjson = json.loads(pagejson)
        except Exception as e:
            logger.error(f"Could not parse {pagejsonpath} {e}")
            if not skiperrors:
                exit(1)
            else:
                continue

    pageconfig = Page(**pageconfigjson)

    pageconfig.date = getdaterange(pageconfig)

    for content in pageconfig.content.keys():
        if not pageconfig.content[content].type == "content":
            continue

        sectionmdpath = f"{page}/{pageconfig.content[content].content}"
        if not os.path.exists(sectionmdpath):
            logger.error(f"{sectionmdpath} does not exist")
            if not skiperrors:
                exit(1)
            pageconfig.content[content].content = ""
        else:
            logger.debug(f"Processing {sectionmdpath}")
            with open(f"{page}/{pageconfig.content[content].content}") as f:
                sectioncontent = f.read()
            pageconfig.content[content].content = markdown2.markdown(sectioncontent)

    pageconfigs[pageurl] = pageconfig

logger.info(f"{len(pageconfigs)} pages found")

for pagepath, pageconfig in pageconfigs.items():
    pagetemplate = templates[siteconfig.themes[pageconfig.theme].templates].get_template("main.lis")
    pagehtml = pagetemplate.render(page = pageconfig, pages = pageconfigs)
    if pagepath != "/":
        os.makedirs(os.path.join(outdir, pagepath.removeprefix("/")))

    if minifyhtml:
        pagehtml = minify_html.minify(pagehtml)

    with open(outdir + pagepath + "index.html", "w") as f:
        f.write(pagehtml)

# Copy static files
shutil.copytree(os.path.join(cfgdir, "static"), os.path.join(outdir, "static"))

pages = [x for x in os.walk(os.path.join(cfgdir, "pages"))]
for page in pages:
    for f in page[2]:
        if pathlib.Path(f).suffix in siteconfig.staticexts:
            pageurl = page[0].removeprefix(cfgdir + "/pages/") + "/"
            if not os.path.exists(os.path.join(outdir, "static/pages", pageurl)):
                os.makedirs(os.path.join(outdir, "static/pages", pageurl))
            shutil.copy(os.path.join(page[0], f), os.path.join(outdir, "static/pages", pageurl, f))

if thumbnails:
    staticdirs = [x for x in os.walk(os.path.join(outdir, "static/pages"))]
    for dirs in staticdirs:
        for f in dirs[2]:
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

# Render styles
for templatedir in [x for x in os.listdir(os.path.join(cfgdir, "templates"))]:
    templates[templatedir] = Environment(
        loader = FileSystemLoader(os.path.join(cfgdir, "templates", templatedir, "css")),
        autoescape = select_autoescape()
    )

os.makedirs(os.path.join(outdir, "static/styles"))

for name, theme in siteconfig.themes.items():
    themepath = os.path.join(outdir, f"static/styles/{name}.css")

    if not theme.enabled:
        logger.info(f"Theme \"{theme.dispname}\" ({name}) is disabled in config, skipping")
        continue

    template = templates[theme.templates].get_template("main.lis")
    css = template.render(theme = theme, themes = siteconfig.themes)

    if minifycss:
        css = minify_html.minify(css)

    with open(themepath, "w") as f:
        f.write(css)