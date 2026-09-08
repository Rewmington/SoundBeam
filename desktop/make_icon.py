"""生成 SoundBeam 程序图标 soundbeam.ico（+ soundbeam.png）。

设计复用 android/app/src/main/res/drawable/ic_launcher.xml：
蓝底(#1A73E8)圆角方块 + 白色连梁音符（双符头 + 双符杆 + 顶部连梁）。

用法：cd 项目根 && python desktop/make_icon.py
产物：assets/soundbeam.ico（16/24/32/48/64/128/256 多尺寸）、assets/soundbeam.png
"""

import os

from PIL import Image, ImageDraw

BLUE = (0x1A, 0x73, 0xE8, 255)   # #1A73E8
WHITE = (255, 255, 255, 255)


def render(size: int = 256, ss: int = 4) -> Image.Image:
    """带超采样抗锯齿地渲染单张图标；尺寸参数作为最终大小。"""
    big = size * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 蓝底圆角方块，铺满整张（桌面图标要实心，不做留白）
    radius = int(big * 0.15)
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=radius, fill=BLUE)

    # 把 108 视口的矢量坐标映射到带内边距的内容区
    pad = big * 0.06
    scale = (big - 2 * pad) / 108.0

    def P(x: float, y: float) -> tuple[float, float]:
        return (pad + x * scale, pad + y * scale)

    # 两个符头（实心圆），坐标取自矢量
    for cx, cy in [(34, 30), (64, 22)]:
        px, py = P(cx, cy)
        r = 10 * scale
        d.ellipse([px - r, py - r, px + r, py + r], fill=WHITE)

    # 符杆 + 顶部连梁（白粗线，端头画小圆做成圆角）
    lw = max(2, int(6 * scale))
    for x1, y1, x2, y2 in [(44, 40, 44, 84), (74, 32, 74, 76), (44, 40, 74, 32)]:
        ax, ay = P(x1, y1)
        bx, by = P(x2, y2)
        d.line([ax, ay, bx, by], fill=WHITE, width=lw)
        er = lw / 2
        d.ellipse([ax - er, ay - er, ax + er, ay + er], fill=WHITE)
        d.ellipse([bx - er, by - er, bx + er, by + er], fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets = os.path.join(root, "assets")
    os.makedirs(assets, exist_ok=True)

    png_path = os.path.join(assets, "soundbeam.png")
    ico_path = os.path.join(assets, "soundbeam.ico")

    img = render(256)
    img.save(png_path)

    # 多尺寸 ico：Pillow 会按 sizes 从上面这张 256 缩放出各档
    img.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"生成 {png_path}")
    print(f"生成 {ico_path}")


if __name__ == "__main__":
    main()
