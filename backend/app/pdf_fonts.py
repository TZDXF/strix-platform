"""让 strix 自带的 reportlab PDF 渲染器支持中文。

strix.interface.viewer.report_pdf 硬编码使用 Helvetica/Courier（仅 Latin-1），
中文内容会渲染成乱码。这里在生成前注册一个系统里的 CJK 字体，并把该模块的
字体常量替换掉，同时给段落样式加 wordWrap='CJK' 让中文可以任意位置换行。

容器内安装 fonts-wqy-microhei / fonts-wqy-zenhei（TrueType 轮廓）。注意 Debian 的
fonts-noto-cjk 是 CFF（PostScript）轮廓，reportlab 的 TTFont 不支持，会报
"postscript outlines are not supported"。本地开发按平台回退到系统字体（Windows 微软雅黑）。
"""

from __future__ import annotations

import logging
import platform
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFontFile

logger = logging.getLogger(__name__)

_SANS_NAME = "StrixSansCJK"
_SANS_BOLD_NAME = "StrixSansCJK-Bold"
_MONO_NAME = "StrixMonoCJK"

# (regular, bold, mono 可选)：按顺序取第一个存在的路径
_FONT_CANDIDATES: dict[str, list[str]] = {
    "regular": [
        # Debian/Ubuntu（Docker 镜像内安装 fonts-wqy-microhei，TrueType 轮廓）
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        # Windows：微软雅黑
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
    ],
    "bold": [
        # WQY 没有粗体字重，Zen Hei 笔画偏粗，可当粗体用
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
    ],
    # 等宽仅用于代码块；找不到专门的 CJK 等宽字体时回退到正文字体
    "mono": [
        # wqy-microhei.ttc 的第二个子字体就是 WenQuanYi Micro Hei Mono
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ],
}

# TTC 里通常包含多个子字体，优先选简中子字体，避免日文字形。
_SUBFONT_PREFERENCE = ("SC", "CN")


def _pick_subfont(path: str, *, mono: bool = False) -> int:
    """在 TTC 中找到简体中文字体对应的 subfontIndex，找不到则返回 0。

    优先选简中子字体（避免日文字形），其次按目标挑选等宽/非等宽子字体
    （wqy-microhei.ttc 内就是 regular + mono 两个子字体）。
    """
    names: list[str] = []
    for index in range(32):
        try:
            face = TTFontFile(path, subfontIndex=index)
        except Exception:  # noqa: BLE001 - 越界等异常说明子字体枚举完了
            break
        names.append(str(getattr(face, "name", b"")) + " " + str(getattr(face, "familyName", b"")))

    def score(name: str) -> tuple[int, int]:
        low = name.lower()
        lang = next((2 for tag in _SUBFONT_PREFERENCE if tag.lower() in low), 0)
        shape = 1 if ("mono" in low) == mono else 0
        return lang, shape

    return max(range(len(names)), key=lambda i: score(names[i])) if names else 0


def _register(target: str, font_name: str) -> str | None:
    """注册一类字体，返回实际字体名；找不到可用字体时返回 None。"""
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name  # 已注册过
    for path in _FONT_CANDIDATES[target]:
        if not Path(path).is_file():
            continue
        try:
            index = _pick_subfont(path, mono=(target == "mono")) if path.lower().endswith(".ttc") else 0
            pdfmetrics.registerFont(TTFont(font_name, path, subfontIndex=index))
            return font_name
        except Exception as exc:  # noqa: BLE001 - 该字体不可用（如 CFF 轮廓），换下一个候选
            logger.warning("注册 CJK 字体失败 %s（%s），尝试下一个候选", path, exc)
    logger.warning("未找到可用的 CJK 字体（%s），PDF 中文可能乱码", target)
    return None


def apply_cjk_fonts() -> bool:
    """patch strix 的 PDF 渲染器使用 CJK 字体；返回是否成功。幂等。"""
    from strix.interface.viewer import report_pdf

    if getattr(report_pdf, "_cjk_patched", False):
        return True

    sans = _register("regular", _SANS_NAME)
    bold = _register("bold", _SANS_BOLD_NAME)
    mono = _register("mono", _MONO_NAME) or sans
    if not sans:
        return False
    if not bold:
        bold = sans
    if not mono:
        mono = sans

    # <b>/<i> 标签按 family 映射切换字形；没有斜体字形，用常规体代替
    pdfmetrics.registerFontFamily(
        sans, normal=sans, bold=bold, italic=sans, boldItalic=bold
    )

    report_pdf._SANS = sans
    report_pdf._SANS_BOLD = bold
    report_pdf._MONO = mono

    # 中文没有空格，reportlab 默认只在空格处断行会导致超长行溢出
    original_styles = report_pdf._styles

    def styles_with_cjk_wrap():
        styles = original_styles()
        for style in styles.values():
            style.wordWrap = "CJK"
        return styles

    report_pdf._styles = styles_with_cjk_wrap
    report_pdf._cjk_patched = True
    logger.info("PDF 中文字体已启用: sans=%s bold=%s mono=%s (%s)", sans, bold, mono, platform.system())
    return True
