# StaticSite
# File: build.py
# Purpose: Read configuration file and build static website
# Created: December 16, 2024
# Modified: May 18, 2025

import argparse
import json
import logging
import os
import pathlib
import re
import shlex
import shutil
import xml.etree.ElementTree as etree
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Union

import markdown
import minify_html
from lysine import Environment, FileSystemLoader, select_autoescape
from markdown.blockprocessors import BlockProcessor
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from PIL import Image
from pydantic import BaseModel, Field, SkipValidation

def parse_attrs(attr_string):
    """Parses attributes from a string like: title="Note" color='blue'"""
    lexer = shlex.shlex(attr_string, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    attrs = {}
    for token in lexer:
        if '=' in token:
            key, val = token.split('=', 1)
            attrs[key] = val.strip('"').strip("'")
    return attrs

# Match :::box or :::info followed by optional key="value" attributes
class BoxedSectionProcessor(BlockProcessor):
    RE_FENCE_START = re.compile(r"^:::(\w+)(.*)$")
    RE_FENCE_END = re.compile(r"^:::\s*$")

    def test(self, parent, block):
        return bool(self.RE_FENCE_START.match(block.strip()))

    def run(self, parent, blocks):
        block = blocks.pop(0)
        m = self.RE_FENCE_START.match(block.strip())
        if not m:
            return
        
        # e.g. "box", "info"
        tag_type = m.group(1)
        # e.g. title="Note"
        attr_string = m.group(2)
        attrs = parse_attrs(attr_string)

        div = etree.SubElement(parent, "div")
        boxed = attrs.pop("boxed", None)

        if boxed:
            div.set("class", f"section boxed")
        else:
            div.set("class", f"section")

        title_text = attrs.pop("title", None)
        if title_text:
            h = etree.SubElement(div, "h3")
            h.text = title_text

        for key, val in attrs.items():
            div.set(key, val)

        content_lines = []
        while blocks:
            line = blocks.pop(0)
            if self.RE_FENCE_END.match(line.strip()):
                break
            content_lines.append(line)

        self.parser.parseBlocks(div, content_lines)

class BoxedSectionExtension(Extension):
    def extendMarkdown(self, md):
        md.parser.blockprocessors.register(BoxedSectionProcessor(md.parser), 'boxedsection', 175)

class ButtonTemplateInlineProcessor(InlineProcessor):
    def handleMatch(self, m, data):
        raw_params = m.group(1)
        # Parse parameters from MediaWiki-like format
        params = {}
        for param in raw_params.split("|"):
            if "=" in param:
                key, value = param.split("=", 1)
                params[key.strip()] = value.strip()

        title = params.get("title", "")
        icon = params.get("icon", "")
        icontitle = params.get("icontitle", "")
        description = params.get("description", "")
        url = params.get("url", "#")
        date = params.get("date", "")

        buttondiv = etree.Element("div")
        buttondiv.set("class", "linklistlink")

        if icon:
            icon_img = etree.SubElement(buttondiv, "img")
            icon_img.set("src", icon)
            icon_img.set("title", icontitle)
            icon_img.set("alt", icontitle)

        a = etree.SubElement(buttondiv, "a")
        a.set("href", url)

        title_span = etree.SubElement(a, "h3")
        title_span.text = title

        if description:
            desc_span = etree.SubElement(a, "p")
            desc_span.set("class", "desc")
            desc_span.text = description

        if date:
            date_span = etree.SubElement(a, "p")
            date_span.set("class", "date")
            date_span.text = date

        return buttondiv, m.start(0), m.end(0)

class ButtonTemplateExtension(Extension):
    def extendMarkdown(self, md):
        # Match {{button|...}}
        BUTTON_RE = r'\{\{button\|(.+?)\}\}'
        md.inlinePatterns.register(ButtonTemplateInlineProcessor(BUTTON_RE, md), 'buttontemplate', 175)

class Page(BaseModel):
    # Page title displayed in <title> and other places
    title: str
    # Page theme
    theme: str = "default"
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
        pagepath = pagedir[0].replace(os.path.join(cfgdir, "pages"), "")

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

        pages[pagepath] = page

    return pages

def buildpages(pages: Dict[str, Page]):
    templates = {}
    for templatedir in [x for x in os.listdir(os.path.join(cfgdir, "templates"))]:
        templates[templatedir] = Environment(
            loader = FileSystemLoader(os.path.join(cfgdir, "templates", templatedir, "html")),
            autoescape = select_autoescape()
        )

    for pagepath, page in pages.items():
        logger.debug(f"Building page {pagepath}")
        # Either set to a custom value or defaulted to "main.lis" beforehand
        # TODO: Should be renamed from htmloverride
        theme = page.theme
        tmpl = page.htmloverride

        if not theme in templates:
            logger.error(f"Theme {theme} not found")
            continue

        tmplenv = templates[theme]

        if not tmpl in templates[theme].list_templates():
            logger.error(f"Template {tmpl} not found in theme {theme}")
            continue

        contentpath = os.path.join(cfgdir, "pages", pagepath.lstrip("/"), page.content)

        if os.path.exists(contentpath):
            logger.debug(f"Processing {page.content}")
            with open(os.path.join(contentpath)) as f:
                content = f.read()
            renderedmd = markdown.markdown(content, extensions = [BoxedSectionExtension(), ButtonTemplateExtension()])
        else:
            logger.error(f"File not found: {contentpath} for {pagepath}")
            continue

        renderedhtml = tmplenv.get_template(tmpl).render(
            page = page,
            content = renderedmd
        )

        if siteconfig.minifyhtml:
            renderedhtml = minify_html.minify(renderedhtml)

        if not os.path.exists(os.path.join(outdir, pagepath.lstrip("/"))):
            os.mkdir(os.path.join(outdir, pagepath.lstrip("/")))

        with open(os.path.join(outdir, pagepath.lstrip("/"), "index.html"), "w") as f:
            f.write(renderedhtml)

# Only copy static files from pages that were registered
def gatherstatic(pages: Dict[str, Page]):
    for pagepath in pages.keys():
        source_dir = os.path.join(cfgdir, "pages", pagepath.lstrip("/"))
        dest_dir = os.path.join(outdir, "static/pages", pagepath.lstrip("/"))

        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir, exist_ok=True)

        for root, _, files in os.walk(source_dir):
            for file in files:
                if file == "page.json" or file == pages[pagepath].content:
                    continue

                # Get file extension
                suffix = pathlib.Path(file).suffix

                # Check if extension is registered in staticexts
                if suffix in siteconfig.staticexts:
                    source_file = os.path.join(root, file)
                    # Get relative path from source_dir
                    rel_path = os.path.relpath(source_file, source_dir)
                    dest_file = os.path.join(dest_dir, rel_path)

                    os.makedirs(os.path.dirname(dest_file), exist_ok=True)

                    logger.debug(f"Copying static file {source_file} to {dest_file}")
                    shutil.copy2(source_file, dest_file)

# Only thumbnail images from pages that were registered
def thumbnails(pages: Dict[str, Page]):
    for page in pages.keys():
        for files in os.listdir():
            suffix = pathlib.Path(files).suffix
            nosuffix = pathlib.Path(files).stem

            if suffix not in siteconfig.staticexts:
                continue

            if siteconfig.staticexts[suffix] != "image":
                continue

            img = Image.open(os.path.join(dirs[0], f))
            if img.size[1] <= thumbsize:
                continue

            hratio = (thumbsize / float(img.size[1]))
            wsize = int((float(img.size[0]) * float(hratio)))

            logger.debug(f"Resizing {f} to {wsize}x{thumbsize}")
            img = img.resize((wsize, thumbsize), thumbalgo)

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
    if not os.path.exists(os.path.join(cfgdir, "templates")):
        logger.critical(f"\"templates\" directory not found in {cfgdir}")
        exit(1)

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
    # Copy static files
    if os.path.exists(os.path.join(outdir, "static")):
        shutil.rmtree(os.path.join(outdir, "static"))
    if os.path.exists(os.path.join(cfgdir, "static")):
        shutil.copytree(os.path.join(cfgdir, "static"), os.path.join(outdir, "static"))
    else:
        logger.critical("\"static\" directory not found in config directory")
        exit(1)

    # Copy robots.txt
    if os.path.exists(os.path.join(outdir, "robots.txt")):
        os.remove(os.path.join(outdir, "robots.txt"))

    if not os.path.exists(os.path.join(cfgdir, "robots.txt")):
        logger.warning("\"robots.txt\" not found in config directory, it would not be available")
    else:
        shutil.copy(os.path.join(cfgdir, "robots.txt"), os.path.join(outdir, "robots.txt"))

    pages: Dict[str, Page] = findpages()
    logger.info(f"Found {len(pages)} pages")

    buildpages(pages)

    gatherstatic(pages)

    if siteconfig.thumbnails:
        thumbnails(pages)

    if siteconfig.drawio:
        drawio()

    buildcss()

    if siteconfig.sitemap:
        sitemap(pages)