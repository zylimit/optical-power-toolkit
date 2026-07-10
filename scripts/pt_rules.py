"""FAT 命名规则引擎 + 光功率合格判定 + 地址标准化。

FAT 名语法（例 DSTC1Z3H2L1S4）:
  AREA  cluster简写，只含字母，不含数字（OCR易错: D/O->0, B->8, I/J->1）
  C#    固定 C + 1 位数字
  Z#    固定 Z + 1 位数字（Z 常被读成 2）
  H#    固定 H + 数字（可能 HB2/H02 等不规范 -> 清洗成 H+int）
  L#    固定 L + 1 位数字，范围 1~8
  S#    固定 S + 1 位数字，范围 1~4（即 FAT/subbox/endbox 端口）

功率合格: -25 < value <= -10 为合格；value<=-25(偏弱) 或 value>-10(偏强) 为错误。
（库里 power 存的是幅值正数，value = -幅值）
"""

import re

# OCR 混淆：在“字母位”上，这些数字很可能是被误读的字母
_DIGIT2ALPHA = {"0": "O", "8": "B", "1": "I", "2": "Z", "5": "S", "6": "G"}
# 完整语法：AREA C# Z# H# L# S#（Z 前缀可选，部分 cluster 写成 C2_2 省了 Z）
_FAT_FULL = re.compile(r"^([A-Z]+)C(\d)Z?(\d)H(?:B)?0*(\d+)L(\d)S(\d)$")
# 只有后半段：H# L# S#（前缀在 cluster 列，由 derive 补）
_FAT_HLS = re.compile(r"^H(?:B)?0*(\d+)L(\d)S(\d)$")
# 解析 cluster 列文本，如 "Iju C1 Z3" -> area/C/Z
_CLU_RE = re.compile(r"([A-Za-z]+)\D*C?\s*(\d)\D*Z?\s*(\d)", re.I)


def _clean(s):
    return re.sub(r"[^A-Za-z0-9]", "", str(s or "")).upper()


def _mk(area, c, z, h, l, s4):
    issues = []
    if not (1 <= int(l) <= 8):
        issues.append(f"L超范围({l})")
    if not (1 <= int(s4) <= 4):
        issues.append(f"S超范围({s4})")
    hub = f"H{int(h)}"
    norm = (f"{area}" if area else "") + (f"C{c}Z{z}" if c else "") + f"{hub}L{l}S{s4}"
    return {"raw": "", "valid": len(issues) == 0, "issues": issues,
            "area": area, "cluster": (f"C{c}" if c else None), "zone": (f"Z{z}" if z else None),
            "hub": hub, "level": f"L{l}", "fat": f"S{s4}", "normalized": norm}


# 通用词不是真 area（sheet 名/表头残留），屏蔽掉
_GENERIC_AREA = {"SHEET", "DATA", "NEW", "HP", "SUMMARY", "TEMPLATE", "POWER", "TEST", "PT", "FAT"}


def parse_cluster(s):
    """从 cluster/sheet/文件名尽力补 area/cluster/zone。
    area=字母前缀(通用词除外)；C/Z 从数字尽力取。兼容 'Iju C1 Z3' / 'AGEGE1' / 'Jabi Cluster 2'。"""
    s = str(s or "").strip()
    am = re.match(r"([A-Za-z]+)", s)
    area = am.group(1).upper() if am else None
    if area in _GENERIC_AREA:
        return None, None, None
    nums = re.findall(r"\d", s)
    c = nums[0] if len(nums) >= 1 else None
    z = nums[1] if len(nums) >= 2 else None
    return area, c, z


def _best_hint(*hints):
    for h in hints:
        if parse_cluster(h)[0]:
            return h
    return None


def parse_fat(name, cluster_hint=None):
    """解析 FAT 名 -> 结构化字段。支持完整名与只有 H#L#S# 的写法（后者用 cluster_hint 补前缀）。"""
    s = _clean(name)
    m = _FAT_FULL.match(s)
    if m:
        d = _mk(*m.groups()); d["raw"] = s; return d
    m = _FAT_HLS.match(s)
    if m:
        area = c = z = None
        if cluster_hint:
            area, c, z = parse_cluster(cluster_hint)
        d = _mk(area, c, z, *m.groups()); d["raw"] = s; return d
    return {"raw": s, "valid": False, "issues": ["不符合FAT命名语法"],
            "area": None, "cluster": None, "zone": None, "hub": None, "level": None, "fat": None,
            "normalized": None}


def ocr_match(ocr_name, table_name):
    """把 OCR 读到的标牌名与表格名做“容错”比对（吸收常见误读）。
    返回 'match' / 'mismatch' / 'unreadable'。"""
    t = parse_fat(table_name)
    if not t["normalized"]:
        return "unreadable"
    o = _clean(ocr_name)
    if not o:
        return "unreadable"
    # 先直接按语法解析 OCR
    po = parse_fat(o)
    if po["normalized"] == t["normalized"]:
        return "match"
    # 容错：字母位上的数字回推为字母，Z 位的 2 回推为 Z，再比
    fixed = list(o)
    for i, ch in enumerate(fixed):
        if ch in _DIGIT2ALPHA:
            fixed[i] = _DIGIT2ALPHA[ch]
    if parse_fat("".join(fixed))["normalized"] == t["normalized"]:
        return "match"
    # 退一步：只要 C/Z/H/L/S 的数字部分都对上（忽略 area 字母噪声）就算 match
    def key(d):
        return (d["cluster"], d["zone"], d["hub"], d["level"], d["fat"]) if d["normalized"] else None
    if po["normalized"] and key(po) == key(t):
        return "match"
    if po["normalized"]:
        return "mismatch"
    return "unreadable"


# 派生列（层级 + 合格 + 复测决策），可从库里已有字段现算，无需重跑 OCR
DERIVED_COLS = ["area", "cluster_code", "zone", "hub", "level", "fat", "fat_valid",
                "power_status_", "needs_retest", "retest_reason", "retest_priority"]


def derive(box_name, power_mag, main_issue=None, has_coord=None, photo_status=None,
           cluster=None, sheet=None, src=None):
    """从盒子名+功率+已判定字段，算出层级/合格/复测决策。返回 DERIVED_COLS 的 dict。
    盒子名缺前缀时，依次用 cluster/sheet/文件名兜底补 area。"""
    d = parse_fat(box_name, cluster_hint=_best_hint(cluster, sheet, src))
    ps = power_status(power_mag)
    reason = prio = ""
    no_evidence = (photo_status == "无图") or (main_issue == "无图判Pass")
    if no_evidence:
        reason, prio = "无有效证据", "高"
    elif ps in ("偏弱", "偏强"):
        reason, prio = "功率不合格", "高"
    elif main_issue in ("功率不符", "盒子不符") or not d["valid"]:
        reason, prio = "数据存疑", "中"
    elif has_coord == "否":
        reason, prio = "无坐标", "中"
    elif photo_status == "有图模糊":
        reason, prio = "照片模糊", "低"
    return {
        "area": d["area"], "cluster_code": d["cluster"], "zone": d["zone"],
        "hub": d["hub"], "level": d["level"], "fat": d["fat"],
        "fat_valid": "是" if d["valid"] else "否", "power_status_": ps,
        "needs_retest": "是" if reason else "否",
        "retest_reason": reason, "retest_priority": prio,
    }


def power_status(mag):
    """mag 为功率幅值正数(=|dBm|)。返回 合格/偏弱/偏强/无值。"""
    if mag is None:
        return "无值"
    try:
        m = abs(float(mag))
    except (ValueError, TypeError):
        return "无值"
    if m >= 25:      # value <= -25
        return "偏弱"
    if m < 10:       # value > -10
        return "偏强"
    return "合格"


# ---- 地址标准化 ----
_OFF_RE = re.compile(r"no street name|no name|unnamed", re.I)


def standardize_street(street):
    """无街名时按客户要求：取最近街名并加 Off 前缀。这里只处理'无街名'标记，
    真实的最近街名需由 AI/geocode 提供（street 传入已是最近街名时自动加 Off）。"""
    s = (street or "").strip()
    if not s or _OFF_RE.search(s):
        return ""  # 无有效街名，待补
    if s.lower().startswith("off "):
        return s
    return s
