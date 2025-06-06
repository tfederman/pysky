from datetime import datetime, timezone

import bs4
import markdown

from pysky.models import BskyPost
from pysky.posts.utils import uploadable, uploaded
from pysky.posts.facet import Facet
from pysky.posts.reply import Reply
from pysky.posts.image import Image
from pysky.posts.video import Video
from pysky.posts.external import External


class Post:

    # redundant to have both text and markdown_text because they can both be parsed as markdown?
    def __init__(
        self,
        text=None,
        reply=None,
        client_unique_key=None,
        reply_client_unique_key=None,
        reply_uri=None,
        convert_markdown=False,
        langs=None,
    ):
        self.text = text or ""
        self.facets = []
        self.videos = []
        self.images = []
        self.langs = langs
        self.external = None
        self.reply = reply
        self.reply_uri = reply_uri
        self.client_unique_key = client_unique_key
        self.reply_client_unique_key = reply_client_unique_key
        self.convert_markdown = convert_markdown

    def add(self, obj):

        type_map = {
            Facet: self.add_facet,
            Image: self.add_image,
            Video: self.add_video,
            External: self.add_external,
            list: lambda objs: [self.add(obj) for obj in objs],
        }

        type_map[type(obj)](obj)

    def remove_media(self):
        self.images = []
        self.videos = []
        if self.external:
            self.external.image = None
            self.external.thumb = None

    def add_external(self, external):
        self.external = external

    def add_facet(self, facet):
        self.facets.append(facet)

    def add_video(self, video):
        assert uploadable(video), "video must be a Video object"
        if len(self.images) >= 1:
            raise Exception(f"too many videos added to post")

        self.videos.append(video)

    def add_image(self, image):
        assert uploadable(image), "image must be an Image object"
        if len(self.images) >= 4:
            raise Exception(f"too many images added to post")

        self.images.append(image)

    def add_images(self, images):
        for img in images:
            self.add_image(img)

    def upload_files(self, bsky):
        uploadable_objects = self.images + self.videos + [self.external]
        for uploadable_obj in uploadable_objects:
            if uploadable_obj and not uploaded(uploadable_obj):
                uploadable_obj.upload(bsky)

    def as_dict(self):

        if not all(uploaded(obj) for obj in self.images + self.videos):
            raise Exception("must call Post.upload_files before posting")

        if self.convert_markdown:
            self.convert_markdown_text()

        if not self.reply and self.reply_client_unique_key:
            self.reply = Reply.from_client_unique_key(self.reply_client_unique_key)
        elif not self.reply and self.reply_uri:
            self.reply = Reply.from_uri(self.reply_uri)

        post = {
            "$type": "app.bsky.feed.post",
            "text": self.text or "",
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }

        if isinstance(self.langs, list):
            post["langs"] = self.langs

        if self.reply:
            post["reply"] = self.reply.as_dict()

        if self.facets:
            post["facets"] = [f.as_dict() for f in self.facets]

        if self.external:
            assert not self.videos and not self.images
            post["embed"] = self.external.as_dict()

        if self.videos:
            assert not self.external and not self.images
            post["embed"] = self.videos[0].as_dict()

        if self.images:
            assert not self.videos and not self.external
            post["embed"] = {
                "$type": "app.bsky.embed.images",
                "images": [image.as_dict() for image in self.images],
            }

        return post

    def convert_markdown_text(self):

        soup = bs4.BeautifulSoup(markdown.markdown(self.text), "html.parser")

        for match in soup.find_all("code"):
            match.unwrap()

        text = b""

        for p in soup.find_all(
            ["p", "span", "div", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "code"]
        ):
            for child in p.contents:
                if isinstance(child, bs4.element.NavigableString):
                    text += child.text.encode("utf-8")
                elif isinstance(child, bs4.element.Tag) and child.name == "a":
                    href = child.attrs["href"]
                    child_text = child.text.encode("utf-8")
                    facet = Facet(len(text), len(text) + len(child_text), href)
                    self.add_facet(facet)
                    text += child_text
                elif isinstance(child, bs4.element.Tag) and child.name == "img":
                    src = child.attrs.get("src")
                    alt = child.attrs.get("alt")
                    if src:
                        self.add_image(Image(src, alt=(alt or "")))
                elif isinstance(child, bs4.element.Tag) and child.name in ["em","strong","i","b"]:
                    text += child.text.encode("utf-8")

            text += b"\n"

        self.text = text.decode("utf-8").strip()

    def save_to_database(self, response):
        create_kwargs = {
            "apilog": response.apilog,
            "cid": response.cid,
            "repo": response.apilog.request_did,
            "uri": response.uri,
            "client_unique_key": self.client_unique_key,
            "reply_to": getattr(self.reply, "uri", None),
        }
        BskyPost.create(**create_kwargs)
