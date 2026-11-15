"""图片缩略图。

收到的图片要能在聊天气泡里直接看到，所以这里做三件事：
  1. 判断一个文件是不是图片（先看扩展名，再用 Pillow 试打开）；
  2. 等比缩放到气泡内能放下的尺寸；
  3. 把圆角外的区域用页面底色填掉，再输出**不透明** PNG。

第 3 步是刻意的：Tk 的 PhotoImage 对带 alpha 的 PNG 在不同平台表现不一致，
有的把透明像素画成黑块。先在 PIL 里合成到底色上，显示就永远正确。
"""
from __future__ import annotations

import io
from pathlib import Path

try:
    from PIL import Image, ImageDraw
    _HAS_PIL = True
except Exception:                                    # pragma: no cover
    Image = None                                     # type: ignore
    ImageDraw = None                                 # type: ignore
    _HAS_PIL = False


IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".jfif", ".gif", ".bmp", ".webp",
    ".tif", ".tiff", ".ico", ".ppm", ".pgm", ".pbm", ".tga",
}

# 巨图直接解码容易吃满内存，超过这个边长先降采样
_MAX_DECODE_EDGE = 4200


def has_pil() -> bool:
    return _HAS_PIL


def looks_like_image(path: str | Path) -> bool:
    p = Path(path)
    if p.suffix.lower() in IMAGE_EXTS:
        return True
    if not _HAS_PIL or not p.is_file():
        return False
    try:
        with Image.open(p) as im:
            im.verify()
        return True
    except Exception:
        return False


def thumbnail_png(
    path: str | Path,
    max_w: int = 252,
    max_h: int = 252,
    *,
    radius: int = 12,
    bg: str = "#1e2532",
    border: str | None = None,
) -> tuple[bytes, int, int] | None:
    """生成缩略图 PNG 字节，返回 (png_bytes, w, h)；失败返回 None。"""
    if not _HAS_PIL:
        return None
    p = Path(path)
    if not p.is_file():
        return None

    try:
        with Image.open(p) as im:
            im.load()
            # 动图取第一帧，ICC/EXIF 方向先纠正
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)
            from PIL import ImageOps
            im = ImageOps.exif_transpose(im)

            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                # 透明处垫底色，避免缩略图出现黑洞
                canvas = Image.new("RGBA", im.size, _rgb(bg) + (255,))
                canvas.alpha_composite(im)
                im = canvas
            else:
                im = im.convert("RGB")

            w0, h0 = im.size
            if max(w0, h0) > _MAX_DECODE_EDGE:
                k = _MAX_DECODE_EDGE / max(w0, h0)
                im = im.resize((max(1, int(w0 * k)), max(1, int(h0 * k))),
                               Image.LANCZOS)
                w0, h0 = im.size
            if w0 < 1 or h0 < 1:
                return None

            scale = min(max_w / w0, max_h / h0, 1.0)
            tw = max(1, int(round(w0 * scale)))
            th = max(1, int(round(h0 * scale)))
            im = im.resize((tw, th), Image.LANCZOS)

            out = Image.new("RGB", (tw, th), _rgb(bg))
            if radius > 0 and tw > 6 and th > 6:
                mask = Image.new("L", (tw, th), 0)
                ImageDraw.Draw(mask).rounded_rectangle(
                    (0, 0, tw - 1, th - 1), radius=radius, fill=255)
                out.paste(im, (0, 0), mask)
                if border:
                    ImageDraw.Draw(out).rounded_rectangle(
                        (0, 0, tw - 1, th - 1), radius=radius,
                        outline=_rgb(border), width=1)
            else:
                out.paste(im, (0, 0))

            buf = io.BytesIO()
            out.save(buf, format="PNG", optimize=True)
            return buf.getvalue(), tw, th
    except Exception:
        return None


def _rgb(color: str) -> tuple[int, int, int]:
    c = (color or "#000000").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except Exception:
        return (0, 0, 0)


def icon_png(path: str | Path, size: int, bg: str = "#0e1116") -> bytes | None:
    """把图标缩放到指定边长，用于顶栏品牌标记（同样输出不透明 PNG）。"""
    if not _HAS_PIL:
        return None
    try:
        with Image.open(path) as im:
            im.load()
            im = im.convert("RGBA").resize((size, size), Image.LANCZOS)
        out = Image.new("RGB", (size, size), _rgb(bg))
        out.paste(im, (0, 0), im)
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None
