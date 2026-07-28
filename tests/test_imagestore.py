# -*- coding: utf-8 -*-
"""server/imagestore.py 原图磁盘存储层——回归测试。

覆盖两类：
1. 【red 锁定，当前应失败】path traversal 安全缺陷——content_md5 未清洗直接
   拼进路径，恶意值（../../.. 相对逃逸 / 绝对路径）会让 save_image 写到
   IMAGES_DIR 之外。期望 imagestore 拒绝非法 content_md5（抛 ValueError）
   或至少落点仍在 IMAGES_DIR 内。当前代码无此保护 → 这两条测试预期红。
   （red-locks-the-bug：先把缺陷固化为失败测试，之后由 implementer 修绿。）
2. 【正常行为回归，当前应绿】save/get/exists/delete 往返、空目录清理、
   sheet 非法字符清洗、不存在时的返回值——锁住正常路径不被修复破坏。

隔离：monkeypatch server.imagestore.IMAGES_DIR 到 tmp_path，跑完不留文件。
运行：python -m pytest tests/test_imagestore.py -v
"""

import pytest

from server.imagestore import (
    IMAGES_DIR,  # noqa: F401  仅为验证可导入（契约）
    delete_image,
    exists,
    get_image_path,
    save_image,
)
import server.imagestore as imagestore


@pytest.fixture
def images_dir(tmp_path, monkeypatch):
    """把模块级 IMAGES_DIR 指到临时目录，测试互不污染、跑完自动清。

    imagestore 内部 _build_path 在调用时读模块全局 IMAGES_DIR，
    monkeypatch.setattr 改的就是这个全局，故存取一致落到 tmp_path。
    """
    monkeypatch.setattr("server.imagestore.IMAGES_DIR", tmp_path)
    return tmp_path


# --------------------------------------------------------------------------
# 1. red 锁定：path traversal 安全缺陷（当前预期 FAIL）
# --------------------------------------------------------------------------

class TestTraversalRejection:
    """核心 red 测试——恶意 content_md5 不应逃出 IMAGES_DIR。

    缺陷现状：content_md5 未经清洗直接 IMAGES_DIR / content_md5 / ...，
    传入 '../../../../etc/evil' 会写到 IMAGES_DIR 之外。
    期望：save_image 拒绝非法 content_md5（抛 ValueError），
    或落点仍在 IMAGES_DIR 内。二者满足其一即视为已修复。
    """

    def test_save_image_rejects_traversal_content_md5(self, images_dir):
        # 相对逃逸
        try:
            path = save_image("../../../../etc/evil", "s", 1, b"x")
        except ValueError:
            return  # 修复后：拒绝非法输入 → 通过
        # 未抛异常 → 至少落点必须仍在 IMAGES_DIR 内，否则=逃逸=红
        resolved = path.resolve()
        root = images_dir.resolve()
        assert str(resolved).startswith(str(root)), (
            f"content_md5 未校验导致逃逸：写到 {resolved}，"
            f"已逃出 IMAGES_DIR {root}"
        )

    def test_save_image_rejects_absolute_content_md5(self, images_dir):
        # 绝对路径 content_md5（Windows 盘符）——Path / 绝对路径会丢弃左操作数
        try:
            path = save_image("C:/Windows/Temp/evil", "s", 1, b"x")
        except ValueError:
            return  # 修复后：拒绝 → 通过
        resolved = path.resolve()
        root = images_dir.resolve()
        assert str(resolved).startswith(str(root)), (
            f"绝对路径 content_md5 未校验导致逃逸：写到 {resolved}，"
            f"已逃出 IMAGES_DIR {root}"
        )


# --------------------------------------------------------------------------
# 2. 正常行为回归（当前预期 PASS）
# --------------------------------------------------------------------------

class TestRoundtrip:
    """save → get_image_path → exists → 读回，字节一致。"""

    def test_save_then_get_and_read_back(self, images_dir):
        data = b"\xff\xd8\xff\x00binaryjpegbytes"
        path = save_image("md5abc", "Sheet1", 3, data)
        assert path.exists()
        # 落点在 IMAGES_DIR 内
        assert str(path.resolve()).startswith(str(images_dir.resolve()))
        # get_image_path 命中
        got = get_image_path("md5abc", "Sheet1", 3)
        assert got is not None
        assert got.read_bytes() == data
        # exists 命中
        assert exists("md5abc", "Sheet1", 3) is True

    def test_overwrite_same_key_stores_once(self, images_dir):
        p1 = save_image("md5dup", "S", 1, b"first")
        p2 = save_image("md5dup", "S", 1, b"second")
        assert p1 == p2
        assert p2.read_bytes() == b"second"


class TestMissing:
    """不存在时的返回值契约。"""

    def test_get_image_path_missing_returns_none(self, images_dir):
        assert get_image_path("nope", "S", 1) is None

    def test_exists_missing_returns_false(self, images_dir):
        assert exists("nope", "S", 1) is False

    def test_delete_missing_returns_false(self, images_dir):
        assert delete_image("nope", "S", 1) is False


class TestDelete:
    """delete_image 删文件 + 清空目录。"""

    def test_delete_removes_file_and_empty_dirs(self, images_dir):
        save_image("md5del", "SheetX", 5, b"x")
        md5_dir = images_dir / "md5del"
        assert md5_dir.exists()
        assert delete_image("md5del", "SheetX", 5) is True
        # 文件已删
        assert not exists("md5del", "SheetX", 5)
        # 空的 sheet 目录与 content_md5 目录都被清
        assert not md5_dir.exists()

    def test_delete_keeps_nonempty_md5_dir(self, images_dir):
        # 同 content_md5 下两张不同 sheet 的图，删其一后 md5 目录应保留
        save_image("md5keep", "SA", 1, b"a")
        save_image("md5keep", "SB", 1, b"b")
        assert delete_image("md5keep", "SA", 1) is True
        md5_dir = images_dir / "md5keep"
        assert md5_dir.exists()  # 另一 sheet 仍在，md5 目录不清
        assert exists("md5keep", "SB", 1)


class TestSheetCleaning:
    """sheet 非法字符清洗——目录名不含 / 或 :（这些会破路径结构）。"""

    def test_sheet_illegal_chars_sanitized(self, images_dir):
        # iju / C1:Z3 含 / 与 : ——清洗后目录名不得残留
        path = save_image("md5clean", "C1:Z3", 1, b"x")
        # 落点仍在 IMAGES_DIR 内
        assert str(path.resolve()).startswith(str(images_dir.resolve()))
        sheet_dir_name = path.parent.name
        assert "/" not in sheet_dir_name
        assert ":" not in sheet_dir_name
        assert "\\" not in sheet_dir_name

    def test_sheet_with_slash_sanitized(self, images_dir):
        path = save_image("md5clean2", "a/b/c", 1, b"x")
        sheet_dir_name = path.parent.name
        assert "/" not in sheet_dir_name
        assert "\\" not in sheet_dir_name
        # 清洗后仍是单层目录（未被拆成多级）
        assert path.parent.parent == images_dir / "md5clean2"
