"""光功率工具箱 V2.0 原图磁盘存储层。

按 content_md5/sheet/row 三级目录组织：同内容文件图只存一份（同 content_md5
+ sheet + row 的图落到同一路径，重写即覆盖，天然不产生重复文件）。

路径约定：IMAGES_DIR/content_md5/<clean_sheet>/<row>.jpg
- content_md5：文件内容指纹，跨 source_file 复用图
- sheet：作目录名前清洗非法字符（沿用 scripts/pt_ocr.py result_path 的
  re.sub(r"[^0-9A-Za-z]+", "_", s) 思路），原 sheet 语义不变
- row：行号作文件名，int → str(row).jpg

库模块，纯文件 IO 副作用层；不写 stdout 重配置（db.py 的 sys.stdout.reconfigure
是 app 层冗余，库模块不重复）。
"""

import re
from pathlib import Path

# 图存根目录：相对模块位置定位，不依赖 cwd
IMAGES_DIR: Path = Path(__file__).resolve().parent / "images"

# 非字母数字序列压成单个下划线——覆盖 Windows 非法路径字符 / \ : * ? " < > | 及空格等
# 与 scripts/pt_ocr.py result_path 清洗正则一致（V1 已验证不撞车）
_ILLEGAL_RE = re.compile(r"[^0-9A-Za-z]+")

# content_md5 作目录名，不经清洗直接拼路径——库层自保护（defense-in-depth）。
# content_md5 是内容指纹，正常取值仅字母数字，故只需挡路径逃逸字符：
# 路径分隔符 / \、盘符/驱动 :、父目录遍历 ..、以及空串。命中即拒（抛 ValueError），
# 不做静默清洗（清洗会掩盖上游传错，抛错让调用方尽早暴露）。
_TRAVERSAL_RE = re.compile(r"[/\\:]|\.\.")


def _clean_sheet(sheet: str) -> str:
    """清洗 sheet 用作目录名：非字母数字序列 → 单个下划线。

    清洗后值仅用于目录名，原 sheet 语义（DB records.sheet 字段）不变。
    空字符串清洗后为空串，保留由调用方传入非空 sheet 的契约。
    """
    return _ILLEGAL_RE.sub("_", sheet)


def _build_path(content_md5: str, sheet: str, row: int) -> Path:
    """拼接目标图片路径（不保证存在）。

    content_md5 未经清洗直接作目录名，故先校验挡路径逃逸——空串或含
    路径分隔符 / \\、盘符 :、父目录遍历 .. 一律拒绝（抛 ValueError），
    确保落点不逃出 IMAGES_DIR（save/get/exists/delete 都经此，一处挡全部）。
    """
    if not content_md5 or _TRAVERSAL_RE.search(content_md5):
        raise ValueError(f"非法 content_md5: {content_md5!r}")
    return IMAGES_DIR / content_md5 / _clean_sheet(sheet) / f"{row}.jpg"


def save_image(content_md5: str, sheet: str, row: int, data: bytes) -> Path:
    """存图：建 content_md5/<clean_sheet>/ 目录，写 <row>.jpg，返回路径。

    同 content_md5+sheet+row 重写即覆盖（同内容图只存一份）。
    """
    path = _build_path(content_md5, sheet, row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def get_image_path(content_md5: str, sheet: str, row: int) -> Path | None:
    """返回存在则 Path，否则 None。"""
    path = _build_path(content_md5, sheet, row)
    return path if path.exists() else None


def exists(content_md5: str, sheet: str, row: int) -> bool:
    """图片是否存在。"""
    return _build_path(content_md5, sheet, row).exists()


def delete_image(content_md5: str, sheet: str, row: int) -> bool:
    """删图；删后空目录顺手清（sheet 目录、content_md5 目录为空则删）。

    返回是否实际删除了文件（不存在返回 False）。
    """
    path = _build_path(content_md5, sheet, row)
    if not path.exists():
        return False
    path.unlink()
    # sheet 目录空则删
    sheet_dir = path.parent
    if sheet_dir.exists() and not any(sheet_dir.iterdir()):
        sheet_dir.rmdir()
        # content_md5 目录空则删
        md5_dir = sheet_dir.parent
        if md5_dir.exists() and not any(md5_dir.iterdir()):
            md5_dir.rmdir()
    return True
